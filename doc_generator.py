"""
Генератор документов «Требование-накладная» (ф.0510451)
на основе шаблона .xls.

Заполняет жёлтые поля шаблона данными из Transaction.
"""

import os
import io
import xlrd
import xlutils.copy as xlcopy

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, 'template.xls')


def _write_merged(ws, row, col_start, col_end, value, style=None):
    """Записывает значение в объединённую ячейку (или обычную)."""
    if style:
        ws.write(row, col_start, value, style)
    else:
        ws.write(row, col_start, value)


def generate_nakladnaya(txn, org_name=''):
    """
    Генерирует .xls файл «Требование-накладная» для транзакции.

    Жёлтые поля из шаблона:
      R13 C43..C112 — Структурное подразделение
      R16 C43..C112 — Отправитель
      R18 C43..C112 — Получатель
      R25 C43..     — Затребовал (должность + ФИО)
      R27           — Дата затребования
      R34+          — Строки товаров (наименование, ед.изм., кол-во)
      R52           — Отпустил / Получил (должность + ФИО)
      R55           — Даты подписей

    Возвращает байты .xls файла.
    """
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(
            f'Шаблон не найден: {TEMPLATE_PATH}. '
            'Поместите файл template.xls в папку приложения.'
        )

    rb = xlrd.open_workbook(TEMPLATE_PATH, formatting_info=True)
    wb = xlcopy.copy(rb)
    ws = wb.get_sheet(0)

    date_str = txn.date.strftime('%d.%m.%Y') if txn.date else ''

    # ── Номер документа и дата ────────────────────────────
    # R8 C73+ — «ТРЕБОВАНИЕ-НАКЛАДНАЯ №» — номер дописываем рядом
    ws.write(8, 88, txn.document_number)
    # R10 C62 — дата «от»
    ws.write(10, 62, date_str)

    # ── Структурное подразделение (R13 C43) ───────────────
    department = ''
    if txn.type == 'transfer' and txn.to_employee:
        department = txn.to_employee.department or ''
    elif txn.type == 'return' and txn.from_employee:
        department = txn.from_employee.department or ''
    elif txn.type == 'receipt':
        department = 'Склад ИТ-отдела'
    ws.write(13, 43, department)

    # ── Отправитель (R16 C43) ─────────────────────────────
    sender = ''
    if txn.type == 'receipt':
        sender = txn.supplier or ''
    elif txn.type == 'transfer':
        if txn.from_employee:
            sender = f'{txn.from_employee.full_name}, {txn.from_employee.department or ""}'
        else:
            sender = 'Склад ИТ-отдела'
    elif txn.type == 'return':
        if txn.from_employee:
            sender = f'{txn.from_employee.full_name}, {txn.from_employee.department or ""}'
    ws.write(16, 43, sender)

    # ── Получатель (R18 C43) ──────────────────────────────
    receiver = ''
    if txn.type == 'receipt':
        receiver = 'Склад ИТ-отдела'
    elif txn.type == 'transfer':
        if txn.to_employee:
            receiver = f'{txn.to_employee.full_name}, {txn.to_employee.department or ""}'
    elif txn.type == 'return':
        receiver = 'Склад ИТ-отдела'
    ws.write(18, 43, receiver)

    # ── Затребовал (R25 C43 — должность, C88 — ФИО) ──────
    requester_pos = ''
    requester_name = ''
    if txn.type == 'transfer' and txn.to_employee:
        requester_pos = txn.to_employee.position or ''
        requester_name = txn.to_employee.full_name
    elif txn.type == 'return' and txn.from_employee:
        requester_pos = txn.from_employee.position or ''
        requester_name = txn.from_employee.full_name
    ws.write(25, 43, requester_pos)
    ws.write(25, 88, requester_name)

    # ── Дата затребования (R27) ───────────────────────────
    ws.write(27, 0, f'"  {date_str[:2]}  "       {_month_name(txn.date)}        20  {date_str[-2:]}  г.')

    # ── Позиции товаров (начиная с R34) ───────────────────
    row = 34
    for idx, item in enumerate(txn.items):
        eq = item.equipment
        if row > 49:
            break  # шаблон рассчитан на ~16 строк
        # Колонка 0..21 — наименование основное
        ws.write(row, 0, eq.name)
        # Колонка 50..65 — инвентарный номер
        ws.write(row, 50, eq.inventory_number or '')
        # Колонка 66..81 — серийный номер (паспорт)
        ws.write(row, 66, eq.serial_number or '')
        # Колонка 82..96 — ед. измерения наименование
        ws.write(row, 82, eq.unit or 'шт.')
        # Колонка 106..116 — затребовано
        ws.write(row, 106, item.quantity)
        # Колонка 117..127 — отпущено
        ws.write(row, 117, item.quantity)
        row += 1

    # ── Отпустил (R52 C9 — должность, C46 — ФИО) ────────
    releaser_pos = ''
    releaser_name = ''
    if txn.responsible_user:
        releaser_name = txn.responsible_user
    ws.write(52, 9, releaser_pos)
    ws.write(52, 46, releaser_name)

    # ── Получил (R52 C98 — должность, C135 — ФИО) ────────
    receiver_pos = ''
    receiver_name = ''
    if txn.type == 'transfer' and txn.to_employee:
        receiver_pos = txn.to_employee.position or ''
        receiver_name = txn.to_employee.full_name
    elif txn.type == 'receipt':
        if txn.responsible_user:
            receiver_name = txn.responsible_user
    elif txn.type == 'return':
        if txn.responsible_user:
            receiver_name = txn.responsible_user
    ws.write(52, 98, receiver_pos)
    ws.write(52, 135, receiver_name)

    # ── Даты подписей (R55) ───────────────────────────────
    date_sig = f'"  {date_str[:2]}  "       {_month_name(txn.date)}        20  {date_str[-2:]}  г.'
    ws.write(55, 0, date_sig)
    ws.write(55, 89, date_sig)

    # ── Сохранить в байты ─────────────────────────────────
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


def _month_name(d):
    """Название месяца на русском в родительном падеже."""
    if not d:
        return '____________'
    months = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря',
    }
    return months.get(d.month, '____________')
