from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date

db = SQLAlchemy()


class Employee(db.Model):
    __tablename__ = 'employees'
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(100), unique=True)           # логин FreeIPA
    full_name = db.Column(db.String(200), nullable=False)
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    position = db.Column(db.String(200))
    department = db.Column(db.String(200))
    email = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    mobile_phone = db.Column(db.String(50))
    employee_number = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    synced_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)


class Equipment(db.Model):
    __tablename__ = 'equipment'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    inventory_number = db.Column(db.String(50), unique=True)
    serial_number = db.Column(db.String(100))
    description = db.Column(db.Text)
    unit = db.Column(db.String(50), default='шт.')
    quantity = db.Column(db.Integer, default=1)
    # Статусы: on_stock, in_use, written_off
    status = db.Column(db.String(20), default='on_stock')
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=True)
    location = db.Column(db.String(200))
    receipt_date = db.Column(db.Date)                       # дата приёмки
    last_operation_date = db.Column(db.Date)                # дата последней операции
    last_responsible = db.Column(db.String(200))            # кто провёл последнюю операцию
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    category = db.relationship('Category', backref='equipment')
    assigned_to = db.relationship('Employee', backref='equipment')

    @property
    def status_display(self):
        return {
            'on_stock': 'На складе',
            'in_use': 'В использовании',
            'written_off': 'Списано',
        }.get(self.status, self.status)


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    # Типы: receipt, transfer, return, write_off
    type = db.Column(db.String(20), nullable=False)
    document_number = db.Column(db.String(50), nullable=False)
    date = db.Column(db.Date, default=date.today)
    supplier = db.Column(db.String(200))
    from_employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=True)
    to_employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=True)
    responsible_user = db.Column(db.String(200))            # X-Remote-Name
    responsible_login = db.Column(db.String(100))           # X-Remote-User
    reason = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    from_employee = db.relationship('Employee', foreign_keys=[from_employee_id])
    to_employee = db.relationship('Employee', foreign_keys=[to_employee_id])
    items = db.relationship('TransactionItem', backref='transaction', cascade='all, delete-orphan')

    @property
    def type_display(self):
        return {
            'receipt': 'Приёмка',
            'transfer': 'Передача',
            'return': 'Возврат',
            'write_off': 'Списание',
        }.get(self.type, self.type)


class TransactionItem(db.Model):
    __tablename__ = 'transaction_items'
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'), nullable=False)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)

    equipment = db.relationship('Equipment')


class IpaSettings(db.Model):
    """Настройки подключения к FreeIPA (singleton)."""
    __tablename__ = 'ipa_settings'
    id = db.Column(db.Integer, primary_key=True)
    server_url = db.Column(db.String(500))          # https://ipa.example.com
    username = db.Column(db.String(100))
    password = db.Column(db.String(200))
    user_group = db.Column(db.String(100), default='employees')
    verify_ssl = db.Column(db.Boolean, default=True)
    last_sync = db.Column(db.DateTime)
