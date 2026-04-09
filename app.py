import os
from datetime import date, datetime

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, g,
)

from models import db, Equipment, Category, Employee, Transaction, TransactionItem, IpaSettings
from ipa_sync import test_connection, sync_employees

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'equipment.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-production')

db.init_app(app)

with app.app_context():
    db.create_all()
    # Миграция: добавить новые колонки, если их нет
    from sqlalchemy import inspect as _sa_inspect, text as _sa_text
    _insp = _sa_inspect(db.engine)
    _eq_cols = {c['name'] for c in _insp.get_columns('equipment')}
    with db.engine.connect() as _conn:
        if 'last_operation_date' not in _eq_cols:
            _conn.execute(_sa_text('ALTER TABLE equipment ADD COLUMN last_operation_date DATE'))
        if 'last_responsible' not in _eq_cols:
            _conn.execute(_sa_text("ALTER TABLE equipment ADD COLUMN last_responsible VARCHAR(200)"))
        _conn.commit()
    # Категории по умолчанию
    if Category.query.count() == 0:
        for name in [
            'Ноутбуки', 'Мониторы', 'Клавиатуры', 'Мыши',
            'Системные блоки', 'Принтеры', 'Сетевое оборудование',
            'Комплектующие', 'Периферия', 'Прочее',
        ]:
            db.session.add(Category(name=name))
        db.session.commit()


# ── Авторизация через обратный прокси ─────────────────────
def _decode_header(value):
    """Декодирует заголовок от прокси: WSGI читает latin-1, прокси шлёт UTF-8."""
    if not value:
        return ''
    try:
        return value.encode('latin-1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        return value


@app.before_request
def load_remote_user():
    """Читает заголовки от прозрачного прокси и ищет сотрудника в БД."""
    g.remote_user = _decode_header(request.headers.get('X-Remote-User', ''))
    g.remote_name = _decode_header(request.headers.get('X-Remote-Name', ''))
    g.current_employee = None
    if g.remote_name:
        g.current_employee = Employee.query.filter_by(
            full_name=g.remote_name, is_active=True
        ).first()


@app.context_processor
def inject_remote_user():
    emp = getattr(g, 'current_employee', None)
    return dict(
        remote_user=getattr(g, 'remote_user', ''),
        remote_name=getattr(g, 'remote_name', ''),
        current_employee=emp,
    )


# ── Утилиты ──────────────────────────────────────────────
def generate_doc_number(prefix):
    """Генерирует уникальный номер документа через MAX, а не COUNT."""
    today = date.today().strftime('%Y%m%d')
    pattern = f'{prefix}-{today}-%'
    last = (
        Transaction.query
        .filter(Transaction.document_number.like(pattern))
        .order_by(Transaction.document_number.desc())
        .first()
    )
    if last:
        # Извлекаем порядковый номер из последнего документа
        try:
            last_num = int(last.document_number.rsplit('-', 1)[-1])
        except (ValueError, IndexError):
            last_num = 0
        return f'{prefix}-{today}-{last_num + 1:03d}'
    return f'{prefix}-{today}-001'


def _safe_qty(value, maximum):
    """Безопасно парсит количество: min=1, max=maximum."""
    try:
        qty = int(value)
    except (ValueError, TypeError):
        qty = 1
    if qty < 1:
        qty = 1
    if qty > maximum:
        qty = maximum
    return qty


# ── Главная ──────────────────────────────────────────────
@app.route('/')
def index():
    total = db.session.query(
        db.func.coalesce(db.func.sum(Equipment.quantity), 0)
    ).filter(Equipment.status != 'written_off').scalar()
    on_stock = db.session.query(
        db.func.coalesce(db.func.sum(Equipment.quantity), 0)
    ).filter(Equipment.status == 'on_stock').scalar()
    in_use = db.session.query(
        db.func.coalesce(db.func.sum(Equipment.quantity), 0)
    ).filter(Equipment.status == 'in_use').scalar()
    written_off = db.session.query(
        db.func.coalesce(db.func.sum(Equipment.quantity), 0)
    ).filter(Equipment.status == 'written_off').scalar()
    # Оборудование на уволенных сотрудниках
    dismissed_eq = db.session.query(
        db.func.coalesce(db.func.sum(Equipment.quantity), 0)
    ).join(Employee, Equipment.assigned_to_id == Employee.id).filter(
        Equipment.status == 'in_use',
        Employee.is_active == False,
    ).scalar()
    dismissed_employees = []
    if dismissed_eq:
        dismissed_employees = (
            db.session.query(
                Employee.full_name,
                db.func.sum(Equipment.quantity).label('eq_count'),
            )
            .join(Equipment, Equipment.assigned_to_id == Employee.id)
            .filter(Equipment.status == 'in_use', Employee.is_active == False)
            .group_by(Employee.id)
            .all()
        )

    recent = Transaction.query.order_by(Transaction.created_at.desc()).limit(10).all()
    return render_template('index.html',
                           total=total, on_stock=on_stock,
                           in_use=in_use, written_off=written_off,
                           dismissed_eq=dismissed_eq,
                           dismissed_employees=dismissed_employees,
                           recent=recent)


# ── Оборудование ────────────────────────────────────────
@app.route('/equipment')
def equipment_list():
    status_filter = request.args.get('status', '')
    category_filter = request.args.get('category', '')
    search = request.args.get('search', '')
    query = Equipment.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    if category_filter:
        query = query.filter_by(category_id=int(category_filter))
    if search:
        query = query.filter(
            db.or_(
                Equipment.name.ilike(f'%{search}%'),
                Equipment.inventory_number.ilike(f'%{search}%'),
                Equipment.serial_number.ilike(f'%{search}%'),
            )
        )
    items = query.order_by(Equipment.created_at.desc()).all()
    categories = Category.query.order_by(Category.name).all()
    return render_template('equipment_list.html', items=items,
                           categories=categories,
                           status_filter=status_filter,
                           category_filter=category_filter,
                           search=search)


@app.route('/equipment/<int:eq_id>')
def equipment_view(eq_id):
    eq = Equipment.query.get_or_404(eq_id)
    history = (
        Transaction.query
        .join(TransactionItem)
        .filter(TransactionItem.equipment_id == eq_id)
        .order_by(Transaction.date.desc())
        .all()
    )
    return render_template('equipment_view.html', eq=eq, history=history)


# ── Сотрудники ──────────────────────────────────────────
@app.route('/employees')
def employee_list():
    employees = Employee.query.order_by(Employee.full_name).all()
    ipa = IpaSettings.query.first()
    return render_template('employee_list.html', employees=employees, ipa=ipa)


# ── Категории ────────────────────────────────────────────
@app.route('/categories', methods=['GET', 'POST'])
def category_list():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            if not Category.query.filter_by(name=name).first():
                db.session.add(Category(name=name))
                db.session.commit()
                flash('Категория добавлена', 'success')
            else:
                flash('Такая категория уже существует', 'warning')
        return redirect(url_for('category_list'))
    categories = Category.query.order_by(Category.name).all()
    return render_template('category_list.html', categories=categories)


@app.route('/categories/<int:cat_id>/edit', methods=['POST'])
def category_edit(cat_id):
    cat = Category.query.get_or_404(cat_id)
    name = request.form.get('name', '').strip()
    if name and name != cat.name:
        if Category.query.filter(Category.name == name, Category.id != cat_id).first():
            flash('Такая категория уже существует', 'warning')
        else:
            cat.name = name
            db.session.commit()
            flash('Категория переименована', 'success')
    return redirect(url_for('category_list'))


@app.route('/categories/<int:cat_id>/delete', methods=['POST'])
def category_delete(cat_id):
    cat = Category.query.get_or_404(cat_id)
    count = Equipment.query.filter_by(category_id=cat_id).count()
    if count > 0:
        flash(f'Невозможно удалить: к категории привязано {count} ед. оборудования', 'error')
    else:
        db.session.delete(cat)
        db.session.commit()
        flash('Категория удалена', 'success')
    return redirect(url_for('category_list'))


# ── Настройки (FreeIPA) ──────────────────────────────────
@app.route('/settings', methods=['GET', 'POST'])
def settings():
    ipa = IpaSettings.query.first()
    if ipa is None:
        ipa = IpaSettings(server_url='', username='', password='', user_group='employees')
        db.session.add(ipa)
        db.session.commit()

    if request.method == 'POST':
        action = request.form.get('action', 'save')

        ipa.server_url = request.form.get('server_url', '').strip()
        ipa.username = request.form.get('username', '').strip()
        pwd = request.form.get('password', '').strip()
        if pwd:
            ipa.password = pwd
        ipa.user_group = request.form.get('user_group', 'employees').strip()
        ipa.verify_ssl = 'verify_ssl' in request.form
        db.session.commit()

        if action == 'test':
            ok, msg = test_connection(ipa)
            flash(msg, 'success' if ok else 'error')
        elif action == 'sync':
            ok, msg = sync_employees(ipa)
            flash(msg, 'success' if ok else 'error')
        else:
            flash('Настройки сохранены', 'success')

        return redirect(url_for('settings'))

    return render_template('settings.html', ipa=ipa)


# ── Приёмка (Receipt) — создаёт оборудование ────────────
@app.route('/receipt', methods=['GET', 'POST'])
def receipt():
    if request.method == 'POST':
        cur_emp = g.current_employee

        # Проверяем, что есть хотя бы одна позиция
        names = request.form.getlist('eq_name')
        if not any(n.strip() for n in names):
            flash('Добавьте хотя бы одну позицию', 'error')
            return redirect(url_for('receipt'))

        txn = Transaction(
            type='receipt',
            document_number=generate_doc_number('ПР'),
            date=date.fromisoformat(request.form['date']),
            supplier=request.form.get('supplier') or None,
            from_employee_id=int(request.form['from_employee_id']) if request.form.get('from_employee_id') else None,
            to_employee_id=cur_emp.id if cur_emp else None,
            responsible_user=g.remote_name,
            responsible_login=g.remote_user,
            notes=request.form.get('notes') or None,
        )
        db.session.add(txn)
        db.session.flush()

        cat_ids = request.form.getlist('eq_category_id')
        inv_nums = request.form.getlist('eq_inventory_number')
        ser_nums = request.form.getlist('eq_serial_number')
        units = request.form.getlist('eq_unit')
        quantities = request.form.getlist('eq_quantity')
        descriptions = request.form.getlist('eq_description')

        for i in range(len(names)):
            if not names[i].strip():
                continue
            qty = _safe_qty(quantities[i] if i < len(quantities) else 1, 99999)
            op_date = date.fromisoformat(request.form['date'])
            eq = Equipment(
                name=names[i].strip(),
                category_id=int(cat_ids[i]),
                inventory_number=inv_nums[i].strip() or None,
                serial_number=ser_nums[i].strip() or None,
                unit=units[i].strip() or 'шт.',
                quantity=qty,
                description=descriptions[i].strip() or None,
                status='on_stock',
                receipt_date=op_date,
                last_operation_date=op_date,
                last_responsible=g.remote_name or None,
            )
            db.session.add(eq)
            db.session.flush()
            db.session.add(TransactionItem(
                transaction_id=txn.id,
                equipment_id=eq.id,
                quantity=qty,
            ))

        db.session.commit()
        flash('Приёмка оформлена', 'success')
        return redirect(url_for('document_nakladnaya', txn_id=txn.id))

    categories = Category.query.order_by(Category.name).all()
    employees = Employee.query.filter_by(is_active=True).order_by(Employee.full_name).all()
    return render_template('receipt_form.html',
                           categories=categories,
                           employees=employees,
                           today=date.today().isoformat())


# ── Передача (Transfer) ──────────────────────────────────
@app.route('/transfer', methods=['GET', 'POST'])
def transfer():
    if request.method == 'POST':
        cur_emp = g.current_employee
        to_emp_id = int(request.form['to_employee_id'])

        # Проверяем, что есть хотя бы одна позиция
        eq_ids = request.form.getlist('equipment_id')
        if not any(eid for eid in eq_ids):
            flash('Добавьте хотя бы одну позицию', 'error')
            return redirect(url_for('transfer'))

        txn = Transaction(
            type='transfer',
            document_number=generate_doc_number('ПД'),
            date=date.fromisoformat(request.form['date']),
            from_employee_id=cur_emp.id if cur_emp else None,
            to_employee_id=to_emp_id,
            responsible_user=g.remote_name,
            responsible_login=g.remote_user,
            reason=request.form.get('reason') or None,
            notes=request.form.get('notes') or None,
        )
        db.session.add(txn)
        db.session.flush()

        quantities = request.form.getlist('item_quantity')
        locations = request.form.getlist('item_location')
        has_items = False
        for i, eq_id in enumerate(eq_ids):
            if not eq_id:
                continue
            eq = Equipment.query.get(int(eq_id))
            if not eq:
                continue
            qty = _safe_qty(quantities[i] if i < len(quantities) else 1, eq.quantity)
            loc = locations[i].strip() if i < len(locations) and locations[i] else None

            op_date = date.fromisoformat(request.form['date'])
            if qty < eq.quantity:
                # Частичная передача — отщепляем
                new_eq = Equipment(
                    name=eq.name,
                    category_id=eq.category_id,
                    inventory_number=None,
                    serial_number=eq.serial_number,
                    description=eq.description,
                    unit=eq.unit,
                    quantity=qty,
                    status='in_use',
                    assigned_to_id=to_emp_id,
                    location=loc,
                    receipt_date=eq.receipt_date,
                    last_operation_date=op_date,
                    last_responsible=g.remote_name or None,
                )
                db.session.add(new_eq)
                db.session.flush()
                eq.quantity -= qty
                db.session.add(TransactionItem(
                    transaction_id=txn.id,
                    equipment_id=new_eq.id,
                    quantity=qty,
                ))
            else:
                eq.status = 'in_use'
                eq.assigned_to_id = to_emp_id
                eq.last_operation_date = op_date
                eq.last_responsible = g.remote_name or None
                if loc:
                    eq.location = loc
                db.session.add(TransactionItem(
                    transaction_id=txn.id,
                    equipment_id=eq.id,
                    quantity=qty,
                ))
            has_items = True

        if not has_items:
            db.session.rollback()
            flash('Не выбрано ни одной позиции', 'error')
            return redirect(url_for('transfer'))

        db.session.commit()
        flash('Передача оформлена', 'success')
        return redirect(url_for('document_nakladnaya', txn_id=txn.id))

    equipment = Equipment.query.filter_by(
        status='on_stock'
    ).order_by(Equipment.name).all()
    employees = Employee.query.filter_by(is_active=True).order_by(Employee.full_name).all()
    return render_template('transfer_form.html',
                           equipment=equipment, employees=employees,
                           today=date.today().isoformat())


# ── Возврат (Return) ─────────────────────────────────────
@app.route('/return', methods=['GET', 'POST'])
def return_equipment():
    if request.method == 'POST':
        cur_emp = g.current_employee

        eq_ids = request.form.getlist('equipment_id')
        if not any(eid for eid in eq_ids):
            flash('Добавьте хотя бы одну позицию', 'error')
            return redirect(url_for('return_equipment'))

        txn = Transaction(
            type='return',
            document_number=generate_doc_number('ВЗ'),
            date=date.fromisoformat(request.form['date']),
            from_employee_id=int(request.form['from_employee_id']),
            to_employee_id=cur_emp.id if cur_emp else None,
            responsible_user=g.remote_name,
            responsible_login=g.remote_user,
            reason=request.form.get('reason') or None,
            notes=request.form.get('notes') or None,
        )
        db.session.add(txn)
        db.session.flush()

        quantities = request.form.getlist('item_quantity')
        has_items = False
        for i, eq_id in enumerate(eq_ids):
            if not eq_id:
                continue
            eq = Equipment.query.get(int(eq_id))
            if not eq:
                continue
            qty = _safe_qty(quantities[i] if i < len(quantities) else 1, eq.quantity)

            op_date = date.fromisoformat(request.form['date'])
            if qty < eq.quantity:
                new_eq = Equipment(
                    name=eq.name,
                    category_id=eq.category_id,
                    inventory_number=None,
                    serial_number=eq.serial_number,
                    description=eq.description,
                    unit=eq.unit,
                    quantity=qty,
                    status='on_stock',
                    assigned_to_id=None,
                    location=None,
                    receipt_date=eq.receipt_date,
                    last_operation_date=op_date,
                    last_responsible=g.remote_name or None,
                )
                db.session.add(new_eq)
                db.session.flush()
                eq.quantity -= qty
                db.session.add(TransactionItem(
                    transaction_id=txn.id,
                    equipment_id=new_eq.id,
                    quantity=qty,
                ))
            else:
                eq.status = 'on_stock'
                eq.assigned_to_id = None
                eq.location = None
                eq.last_operation_date = op_date
                eq.last_responsible = g.remote_name or None
                db.session.add(TransactionItem(
                    transaction_id=txn.id,
                    equipment_id=eq.id,
                    quantity=qty,
                ))
            has_items = True

        if not has_items:
            db.session.rollback()
            flash('Не выбрано ни одной позиции', 'error')
            return redirect(url_for('return_equipment'))

        db.session.commit()
        flash('Возврат оформлен', 'success')
        return redirect(url_for('document_nakladnaya', txn_id=txn.id))

    equipment = Equipment.query.filter_by(status='in_use').order_by(Equipment.name).all()
    employees = Employee.query.filter_by(is_active=True).order_by(Employee.full_name).all()
    return render_template('return_form.html',
                           equipment=equipment, employees=employees,
                           today=date.today().isoformat())


# ── Списание (Write-off) ─────────────────────────────────
@app.route('/write-off', methods=['GET', 'POST'])
def write_off():
    if request.method == 'POST':
        eq_ids = request.form.getlist('equipment_id')
        if not any(eid for eid in eq_ids):
            flash('Добавьте хотя бы одну позицию', 'error')
            return redirect(url_for('write_off'))

        txn = Transaction(
            type='write_off',
            document_number=generate_doc_number('СП'),
            date=date.fromisoformat(request.form['date']),
            responsible_user=g.remote_name,
            responsible_login=g.remote_user,
            reason=request.form['reason'],
            notes=request.form.get('notes') or None,
        )
        db.session.add(txn)
        db.session.flush()

        quantities = request.form.getlist('item_quantity')
        has_items = False
        for i, eq_id in enumerate(eq_ids):
            if not eq_id:
                continue
            eq = Equipment.query.get(int(eq_id))
            if not eq:
                continue
            qty = _safe_qty(quantities[i] if i < len(quantities) else 1, eq.quantity)

            op_date = date.fromisoformat(request.form['date'])
            if qty < eq.quantity:
                new_eq = Equipment(
                    name=eq.name,
                    category_id=eq.category_id,
                    inventory_number=None,
                    serial_number=eq.serial_number,
                    description=eq.description,
                    unit=eq.unit,
                    quantity=qty,
                    status='written_off',
                    assigned_to_id=None,
                    location=None,
                    receipt_date=eq.receipt_date,
                    last_operation_date=op_date,
                    last_responsible=g.remote_name or None,
                )
                db.session.add(new_eq)
                db.session.flush()
                eq.quantity -= qty
                db.session.add(TransactionItem(
                    transaction_id=txn.id,
                    equipment_id=new_eq.id,
                    quantity=qty,
                ))
            else:
                eq.status = 'written_off'
                eq.assigned_to_id = None
                eq.last_operation_date = op_date
                eq.last_responsible = g.remote_name or None
                db.session.add(TransactionItem(
                    transaction_id=txn.id,
                    equipment_id=eq.id,
                    quantity=qty,
                ))
            has_items = True

        if not has_items:
            db.session.rollback()
            flash('Не выбрано ни одной позиции', 'error')
            return redirect(url_for('write_off'))

        db.session.commit()
        flash('Списание оформлено', 'success')
        return redirect(url_for('document_view', txn_id=txn.id))

    equipment = Equipment.query.filter(
        Equipment.status != 'written_off'
    ).order_by(Equipment.name).all()
    return render_template('writeoff_form.html',
                           equipment=equipment,
                           today=date.today().isoformat())


# ── Просмотр документа ──────────────────────────────────
@app.route('/document/<int:txn_id>')
def document_view(txn_id):
    txn = Transaction.query.get_or_404(txn_id)
    if txn.type in ('receipt', 'transfer', 'return'):
        return redirect(url_for('document_nakladnaya', txn_id=txn_id))
    return render_template('doc_writeoff.html', txn=txn)


# ── Печатная накладная (ф.0510451) ────────────────────────
@app.route('/document/<int:txn_id>/nakladnaya')
def document_nakladnaya(txn_id):
    """Требование-накладная ф.0510451 — встроена в основной layout."""
    txn = Transaction.query.get_or_404(txn_id)
    if txn.type == 'write_off':
        flash('Для списания накладная не формируется', 'warning')
        return redirect(url_for('document_view', txn_id=txn_id))

    sender_emp = txn.from_employee
    receiver_emp = txn.to_employee

    if txn.type == 'receipt':
        sender_dept = sender_emp.department if sender_emp else (txn.supplier or '')
        receiver_dept = receiver_emp.department if receiver_emp else ''
    else:
        sender_dept = sender_emp.department if sender_emp else ''
        receiver_dept = receiver_emp.department if receiver_emp else ''

    return render_template('doc_nakladnaya.html', txn=txn,
                           sender_emp=sender_emp,
                           receiver_emp=receiver_emp,
                           sender_dept=sender_dept,
                           receiver_dept=receiver_dept)


# ── История ──────────────────────────────────────────────
@app.route('/history')
def history():
    type_filter = request.args.get('type', '')
    query = Transaction.query
    if type_filter:
        query = query.filter_by(type=type_filter)
    transactions = query.order_by(Transaction.date.desc(), Transaction.created_at.desc()).all()
    return render_template('history.html', transactions=transactions, type_filter=type_filter)


# ── API: оборудование сотрудника ─────────────────────────
@app.route('/api/equipment-by-employee/<int:emp_id>')
def api_equipment_by_employee(emp_id):
    """JSON-список оборудования, закреплённого за сотрудником."""
    items = Equipment.query.filter_by(assigned_to_id=emp_id, status='in_use').all()
    return [
        {
            'id': e.id,
            'name': e.name,
            'inventory_number': e.inventory_number or '',
            'serial_number': e.serial_number or '',
            'receipt_date': e.receipt_date.strftime('%d.%m.%Y') if e.receipt_date else '',
            'department': (e.assigned_to.department if e.assigned_to else '') or '',
            'quantity': e.quantity,
            'unit': e.unit or 'шт.',
        }
        for e in items
    ]


# ── API: поиск сотрудников ────────────────────────────────
@app.route('/api/employees/search')
def api_employee_search():
    """Поиск сотрудников по ФИО, должности, отделу.

    ?include_inactive=1  — включить уволенных, у которых есть оборудование
                           (для формы возврата).
    """
    q = request.args.get('q', '').strip()
    include_inactive = request.args.get('include_inactive') == '1'

    if include_inactive:
        # Активные + неактивные, у которых есть in_use оборудование
        inactive_with_eq = (
            db.session.query(Equipment.assigned_to_id)
            .filter(Equipment.status == 'in_use', Equipment.assigned_to_id.isnot(None))
            .distinct()
            .subquery()
        )
        query = Employee.query.filter(
            db.or_(
                Employee.is_active == True,
                Employee.id.in_(db.session.query(inactive_with_eq)),
            )
        )
    else:
        query = Employee.query.filter_by(is_active=True)

    if q:
        query = query.filter(
            db.or_(
                Employee.full_name.ilike(f'%{q}%'),
                Employee.position.ilike(f'%{q}%'),
                Employee.department.ilike(f'%{q}%'),
            )
        )
    employees = query.order_by(Employee.full_name).limit(20).all()
    return [
        {
            'id': e.id,
            'full_name': e.full_name,
            'position': e.position or '',
            'department': e.department or '',
            'is_active': e.is_active,
        }
        for e in employees
    ]


# ── Запуск ────────────────────────────────────────────────
if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ['PORT'])
    app.run(host=host, port=port, debug=False, use_reloader=False)
