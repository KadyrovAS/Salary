import sqlite3
from pathlib import Path
from pdf_parser import Payslip

DB_PATH = Path("payslips.db")

# Месяцы для перевода из текста в число
MONTH_MAP = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

# Соответствие между названиями начислений/удержаний и колонками БД
EARNING_FIELD_MAP = {
    "Оклад по дням": "oklad_po_dnyam",
    "Оплата по окладу": "oklad_po_dnyam",
    "Персональная надбавка": "personalnaya_nadbavka",
    "Персональная научная": "personalnaya_nauchnaya_nadbavka",
    "Надбавка научным": "nadbavka_nauchnym_rabotnikam",
    "Стимулирующая надбавка": "stimuliruyushchaya_nadbavka",
    "Больничный за счет": "bolnichnyy_za_schet_rabotodatelya",
    "Больничный": "bolnichnyy",
    "НДФЛ": "ndfl",
    "Доплата за совмещение": "doplata_za_sovmeshchenie",
}


def get_connection() -> sqlite3.Connection:
    """
    Подключается к базе данных SQLite (создаёт файл, если его нет).
    Возвращает объект соединения.
    """
    return sqlite3.Connection(str(DB_PATH))


def _migrate_schema(conn: sqlite3.Connection):
    """
    Добавляет недостающие колонки в уже существующую таблицу payslips.
    Нужно для обратной совместимости со старыми БД, созданными до
    появления новых видов начислений.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(payslips)")}
    required = {
        "doplata_za_sovmeshchenie": "REAL DEFAULT 0",
    }
    for col, ddl in required.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE payslips ADD COLUMN {col} {ddl}")


def init_db(conn: sqlite3.Connection):
    """
    Создаёт таблицу payslips, если она не существует.
    Таблица содержит уникальный индекс по году и месяцу,
    что исключает дублирование записей.
    Также выполняет миграцию схемы для старых БД.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payslips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            oklad_po_dnyam REAL DEFAULT 0,
            personalnaya_nadbavka REAL DEFAULT 0,
            personalnaya_nauchnaya_nadbavka REAL DEFAULT 0,
            nadbavka_nauchnym_rabotnikam REAL DEFAULT 0,
            stimuliruyushchaya_nadbavka REAL DEFAULT 0,
            bolnichnyy REAL DEFAULT 0,
            bolnichnyy_za_schet_rabotodatelya REAL DEFAULT 0,
            doplata_za_sovmeshchenie REAL DEFAULT 0,
            ndfl REAL DEFAULT 0,
            total_earned REAL DEFAULT 0,
            total_withheld REAL DEFAULT 0,
            total_paid REAL DEFAULT 0,
            amount_to_pay REAL DEFAULT 0,
            UNIQUE(year, month)
        )
    """)
    _migrate_schema(conn)
    conn.commit()


def month_name_to_num(name: str) -> int:
    """
    Преобразует русское название месяца в число (1–12).
    Если месяц не распознан, возвращает 0.
    """
    first_word = name.strip().split()[0].lower() if name.strip() else ""
    return MONTH_MAP.get(first_word, 0)


def _match_earning_field(earning_name: str) -> str | None:
    """
    Сопоставляет имя начисления/удержания из PDF с колонкой базы данных.
    Возвращает имя колонки или None, если совпадения нет.
    """
    for pattern, column in EARNING_FIELD_MAP.items():
        if pattern in earning_name:
            return column
    return None


def record_exists(conn: sqlite3.Connection, year: int, month: int) -> bool:
    """
    Проверяет, существует ли уже запись для указанных года и месяца.
    """
    cursor = conn.execute(
        "SELECT 1 FROM payslips WHERE year = ? AND month = ?",
        (year, month)
    )
    return cursor.fetchone() is not None


def insert_payslip(conn: sqlite3.Connection, slip: Payslip) -> bool:
    """
    Вставляет запись из объекта Payslip в базу данных.
    Благодаря UNIQUE(year, month) дубликаты не добавляются.
    Возвращает True, если запись вставлена; False, если дубликат.
    """
    year = int(slip.period_year) if slip.period_year else 0
    month = month_name_to_num(slip.period_month)

    if month == 0:
        print(f"  ⚠ Не распознан месяц: «{slip.period_month}», пропуск")
        return False

    # Собираем суммы начислений и удержаний по колонкам
    fields = {col: 0.0 for col in EARNING_FIELD_MAP.values()}

    for item in slip.earnings + slip.deductions:
        col = _match_earning_field(item.name)
        if col:
            fields[col] += item.amount

    try:
        conn.execute(
            """
            INSERT INTO payslips (
                year, month,
                oklad_po_dnyam, personalnaya_nadbavka,
                personalnaya_nauchnaya_nadbavka,
                nadbavka_nauchnym_rabotnikam,
                stimuliruyushchaya_nadbavka,
                bolnichnyy, bolnichnyy_za_schet_rabotodatelya,
                doplata_za_sovmeshchenie,
                ndfl,
                total_earned, total_withheld, total_paid, amount_to_pay
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                year, month,
                fields["oklad_po_dnyam"],
                fields["personalnaya_nadbavka"],
                fields["personalnaya_nauchnaya_nadbavka"],
                fields["nadbavka_nauchnym_rabotnikam"],
                fields["stimuliruyushchaya_nadbavka"],
                fields["bolnichnyy"],
                fields["bolnichnyy_za_schet_rabotodatelya"],
                fields["doplata_za_sovmeshchenie"],
                fields["ndfl"],
                slip.total_earned,
                slip.total_withheld,
                slip.total_paid,
                slip.amount_to_pay,
            )
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Дубликат — запись с таким (year, month) уже существует
        return False