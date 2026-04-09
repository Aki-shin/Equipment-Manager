"""
Модуль синхронизации сотрудников с FreeIPA 4.12.2 (API 2.254).

Использует JSON-RPC API FreeIPA:
  1. POST /ipa/session/login_password  — аутентификация
  2. POST /ipa/session/json            — вызовы API (user_find)

Быстрая синхронизация: один вызов user_find(in_group=...) вместо
N отдельных user_show.
"""

import requests
from datetime import datetime
from models import db, Employee, IpaSettings


class IpaClient:
    """Минимальный клиент FreeIPA JSON-RPC."""

    def __init__(self, server_url, username, password, verify_ssl=True):
        self.base = server_url.rstrip('/')
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self._login(username, password)

    def _login(self, username, password):
        resp = self.session.post(
            f'{self.base}/ipa/session/login_password',
            data={'user': username, 'password': password},
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'text/plain',
                'Referer': f'{self.base}/ipa',
            },
        )
        if resp.status_code != 200:
            raise ConnectionError(
                f'Ошибка аутентификации FreeIPA: HTTP {resp.status_code}')

    def _rpc(self, method, args=None, options=None):
        payload = {
            'id': 0,
            'method': method,
            'params': [args or [], options or {}],
        }
        resp = self.session.post(
            f'{self.base}/ipa/session/json',
            json=payload,
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Referer': f'{self.base}/ipa',
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get('error'):
            raise RuntimeError(data['error'].get('message', str(data['error'])))
        return data['result']

    def ping(self):
        return self._rpc('ping')

    def user_find(self, criteria='', **kwargs):
        """Поиск пользователей. Поддерживает in_group, pkey_only, all и др."""
        opts = {'version': '2.254', 'sizelimit': 0, **kwargs}
        result = self._rpc('user_find', [criteria], opts)
        return result.get('result', [])


def _first(val):
    """FreeIPA возвращает массивы — берём первый элемент."""
    if isinstance(val, (list, tuple)):
        return val[0] if val else ''
    return val or ''


def test_connection(settings):
    """
    Проверяет соединение с FreeIPA.
    Возвращает (True, сообщение) или (False, ошибка).
    """
    try:
        client = IpaClient(
            settings.server_url,
            settings.username,
            settings.password,
            settings.verify_ssl,
        )
        client.ping()
        members = client.user_find('', in_group=settings.user_group, pkey_only=True)
        return True, f'Подключение успешно. Участников в группе «{settings.user_group}»: {len(members)}'
    except Exception as exc:
        return False, f'Ошибка: {exc}'


def sync_employees(settings):
    """
    Синхронизирует сотрудников из группы FreeIPA в локальную БД.
    Один вызов user_find(in_group=..., all=True) для всех пользователей.
    Возвращает (True, сообщение) или (False, ошибка).
    """
    try:
        client = IpaClient(
            settings.server_url,
            settings.username,
            settings.password,
            settings.verify_ssl,
        )
        # Один запрос — все пользователи группы со всеми атрибутами
        users = client.user_find('', in_group=settings.user_group, all=True)
        if not users:
            return True, 'Группа пуста — нечего синхронизировать.'

        synced = 0
        now = datetime.utcnow()
        seen_uids = set()

        for user in users:
            uid = _first(user.get('uid', ''))
            if not uid:
                continue
            seen_uids.add(uid)

            full_name = _first(user.get('cn', ''))
            first_name = _first(user.get('givenname', ''))
            last_name = _first(user.get('sn', ''))
            position = _first(user.get('title', ''))
            department = _first(user.get('departmentnumber') or user.get('ou', ''))
            email = _first(user.get('mail', ''))
            phone = _first(user.get('telephonenumber', ''))
            mobile = _first(user.get('mobile', ''))
            emp_num = _first(user.get('employeenumber', ''))

            emp = Employee.query.filter_by(uid=uid).first()
            if emp is None:
                emp = Employee(uid=uid)
                db.session.add(emp)

            emp.full_name = full_name or f'{last_name} {first_name}'.strip() or uid
            emp.first_name = first_name
            emp.last_name = last_name
            emp.position = position
            emp.department = department
            emp.email = email
            emp.phone = phone
            emp.mobile_phone = mobile
            emp.employee_number = emp_num
            emp.is_active = True
            emp.synced_at = now
            synced += 1

        # Деактивировать тех, кого больше нет в группе
        deactivated_with_eq = []
        if seen_uids:
            to_deactivate = Employee.query.filter(
                Employee.uid.isnot(None),
                ~Employee.uid.in_(seen_uids),
                Employee.is_active == True,
            ).all()
            for emp in to_deactivate:
                emp.is_active = False
                # Проверяем, есть ли на сотруднике оборудование
                eq_count = Equipment.query.filter_by(
                    assigned_to_id=emp.id, status='in_use'
                ).count()
                if eq_count > 0:
                    deactivated_with_eq.append(f'{emp.full_name} ({eq_count} ед.)')

        settings.last_sync = now
        db.session.commit()

        msg = f'Синхронизировано: {synced} сотрудник(ов).'
        if deactivated_with_eq:
            msg += (
                f'\n⚠ Внимание! На уволенных сотрудниках числится оборудование: '
                + ', '.join(deactivated_with_eq)
                + '. Оформите возврат.'
            )
        return True, msg
    except Exception as exc:
        db.session.rollback()
        return False, f'Ошибка: {exc}'


def find_employee_by_name(full_name):
    """Ищет сотрудника по полному имени (X-Remote-Name)."""
    if not full_name:
        return None
    return Employee.query.filter_by(full_name=full_name, is_active=True).first()
