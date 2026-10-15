import pymupdf as fitz
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PayslipItem:
    """Строка таблицы начислений, удержаний или выплат."""
    name: str
    period: str = ""
    days: str = ""
    hours: str = ""
    amount: float = 0


@dataclass
class Payslip:
    """Структурированные данные расчётного листка."""
    organization: str = ""
    period_month: str = ""
    period_year: str = ""
    employee_name: str = ""
    employee_id: str = ""
    amount_to_pay: float = 0
    position: str = ""
    department: str = ""
    salary_rate: float = 0
    total_earned: float = 0
    total_withheld: float = 0
    total_paid: float = 0
    total_taxable_income: float = 0
    child_deductions: float = 0
    earnings: List[PayslipItem] = field(default_factory=list)
    deductions: List[PayslipItem] = field(default_factory=list)
    payments: List[PayslipItem] = field(default_factory=list)
    raw_text: str = ""


def extract_text(pdf_bytes: bytes) -> str:
    """
    Извлекает весь текст из PDF, переданного как байты.
    Возвращает строку с текстом всех страниц.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_parts = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text_parts.append(page.get_text())
    doc.close()
    return "\n".join(text_parts)


def _parse_amount(text: str) -> float:
    """
    Преобразует строку с суммой (например, '99 386,00') в float.
    Удаляет пробелы-разделители, заменяет запятую на точку.
    """
    cleaned = text.strip().replace("\xa0", " ").replace(" ", "")
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _find_value(lines: List[str], key: str) -> str:
    """
    Ищет значение после строки с указанным ключом.
    Например, для key='Оклад (тариф):' вернёт '28 028'.
    """
    for i, line in enumerate(lines):
        if key in line:
            if ":" in line:
                val = line.split(":", 1)[1].strip()
                if val:
                    return val
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    return ""


def _preprocess_lines(lines: List[str]) -> List[str]:
    """
    Разделяет строки, в которых сумма начисления и название
    удержания/выплаты находятся на одной строке.
    Например: '36 000,00 Выплачено:' → ['36 000,00', 'Выплачено:']
    Также разделяет '107 693,80 Удержано:' → ['107 693,80', 'Удержано:']
    """
    right_col_keywords = [
        "НДФЛ", "Выплачено", "За первую половину месяца",
        "Начисление зарплаты", "Больничные листы", "Удержано",
    ]
    result = []
    for line in lines:
        # Ищем сумму в начале строки, за которой идёт текст
        m = re.match(r"^([\d\s]+[,.]\d{2})\s+(.+)$", line)
        if m:
            amount_str = m.group(1)
            rest = m.group(2).strip()
            is_right_col = any(rest.startswith(kw) for kw in right_col_keywords)
            if is_right_col:
                result.append(amount_str)
                result.append(rest)
                continue
        result.append(line)
    return result


def _parse_earnings_and_deductions(lines: List[str]) -> tuple:
    """
    Разбирает таблицу начислений, удержаний и выплат.
    Возвращает три списка: earnings, deductions, payments.
    """
    earnings = []
    deductions = []
    payments = []

    deduction_keywords = ["НДФЛ"]
    payment_keywords = [
        "Выплачено", "За первую половину месяца",
        "Начисление зарплаты", "Больничные листы",
    ]
    earning_names = [
        "Персональная надбавка", "Оклад по дням", "Оплата по окладу",
        "Персональная научная", "Надбавка научным",
        "Стимулирующая надбавка", "Больничный",
        "Доплата за совмещение",
    ]

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # Проверяем правую колонку (удержание/выплата)
        right_col = False
        for kw in deduction_keywords + payment_keywords:
            if line.startswith(kw):
                right_col = True
                break

        if right_col:
            name = line
            period = ""
            amount = 0.0

            j = i + 1
            collected = []
            while j < len(lines) and j < i + 4:
                next_line = lines[j].strip()
                if not next_line:
                    j += 1
                    continue
                is_new_item = False
                for kw in earning_names + deduction_keywords + payment_keywords:
                    if next_line.startswith(kw):
                        is_new_item = True
                        break
                if is_new_item:
                    break
                collected.append(next_line)
                j += 1

            for item in collected:
                if re.match(r"^[А-Яа-я]+\.\s*\d{4}$", item) or re.match(r"^\d{2}\.\d{2}-\d{2}\.\d{2}$", item):
                    period = item
                elif re.match(r"^[\d\s]+,\d{2}$", item) or re.match(r"^[\d\s]+\.\d{2}$", item):
                    amount = _parse_amount(item)

            item = PayslipItem(name=name, period=period, amount=amount)
            if any(kw in name for kw in deduction_keywords):
                deductions.append(item)
            else:
                payments.append(item)
            i = j
            continue

        # Проверяем левую колонку (начисление)
        is_earning = any(line.startswith(kw) for kw in earning_names)

        if is_earning:
            name = line
            # Если имя переносится на следующую строку
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and not re.match(r"^[\dА-Яа-я]+\.", next_line) and not re.match(r"^\d", next_line):
                    if not re.match(r"^[А-Яа-я]+\.\s*\d{4}$", next_line) and not re.match(r"^\d{2}\.\d{2}", next_line):
                        name = f"{name} {next_line}"
                        i += 1

            period = ""
            days = ""
            hours = ""
            amount = 0.0

            j = i + 1
            collected = []
            while j < len(lines) and j < i + 6:
                next_line = lines[j].strip()
                if not next_line:
                    j += 1
                    continue
                is_new_item = False
                for kw in earning_names + deduction_keywords + payment_keywords:
                    if next_line.startswith(kw):
                        is_new_item = True
                        break
                if is_new_item:
                    break
                collected.append(next_line)
                j += 1

            for item in collected:
                if re.match(r"^[А-Яа-я]+\.\s*\d{4}$", item) or re.match(r"^\d{2}\.\d{2}-\d{2}\.\d{2}$", item):
                    period = item
                elif re.match(r"^\d{1,2}\s*$", item):
                    if not days:
                        days = item
                    elif not hours:
                        hours = item
                elif re.match(r"^[\d\s]+,\d{2}$", item) or re.match(r"^[\d\s]+\.\d{2}$", item):
                    amount = _parse_amount(item)

            earnings.append(PayslipItem(
                name=name, period=period, days=days, hours=hours, amount=amount
            ))
            i = j
            continue

        i += 1

    return earnings, deductions, payments


def parse_payslip(pdf_bytes: bytes) -> Payslip:
    """
    Главный метод: принимает байты PDF, извлекает текст,
    парсит его и возвращает структурированный объект Payslip.
    """
    raw_text = extract_text(pdf_bytes)
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]

    # Предобработка: разделяем суммы и ключевые слова на одной строке
    lines = _preprocess_lines(lines)

    slip = Payslip(raw_text=raw_text)

    # Организация
    org = _find_value(lines, "Организация:")
    if not org:
        for i, line in enumerate(lines):
            if line == "Организация:" and i + 1 < len(lines):
                org = lines[i + 1]
                break
    slip.organization = org.strip()

    # Период — "РАСЧЕТНЫЙ ЛИСТОК ЗА СЕНТЯБРЬ 2022"
    for line in lines:
        m = re.match(r"РАСЧЕТНЫЙ\s+ЛИСТОК\s+ЗА\s+(\S+)\s+(\d{4})", line, re.IGNORECASE)
        if m:
            slip.period_month = m.group(1)
            slip.period_year = m.group(2)
            break

    # ФИО и табельный номер
    for line in lines:
        m = re.match(r"^(.+?)\s*$(\d+)\s*$", line)
        if m and "Организация" not in line:
            slip.employee_name = m.group(1).strip()
            slip.employee_id = m.group(2).strip()
            break

    # К выплате
    for i, line in enumerate(lines):
        if "К выплате:" in line:
            if ":" in line:
                val = line.split(":", 1)[1].strip()
                if val:
                    slip.amount_to_pay = _parse_amount(val)
            elif i + 1 < len(lines):
                slip.amount_to_pay = _parse_amount(lines[i + 1])
            break

    # Должность
    slip.position = _find_value(lines, "Должность:")

    # Подразделение
    for i, line in enumerate(lines):
        if "Подразделение:" in line:
            val = line.split(":", 1)[1].strip()
            if val:
                slip.department = val
            elif i + 1 < len(lines):
                slip.department = lines[i + 1]
            break

    # Оклад (тариф)
    rate = _find_value(lines, "Оклад (тариф):")
    slip.salary_rate = _parse_amount(rate) if rate else 0

    # Начислено / Удержано / Выплачено
    for i, line in enumerate(lines):
        if "Начислено:" in line:
            val = line.split(":", 1)[1].strip()
            if val:
                slip.total_earned = _parse_amount(val.split()[0])
            elif i + 1 < len(lines):
                slip.total_earned = _parse_amount(lines[i + 1])
        if "Удержано:" in line:
            val = line.split(":", 1)[1].strip()
            if val:
                slip.total_withheld = _parse_amount(val)
            elif i + 1 < len(lines):
                slip.total_withheld = _parse_amount(lines[i + 1])
        if "Выплачено:" in line:
            val = line.split(":", 1)[1].strip()
            if val:
                slip.total_paid = _parse_amount(val)
            elif i + 1 < len(lines):
                slip.total_paid = _parse_amount(lines[i + 1])

    # Общий облагаемый доход
    for line in lines:
        m = re.search(r"Общий облагаемый доход:\s*([\d\s]+,\d{2})", line)
        if m:
            slip.total_taxable_income = _parse_amount(m.group(1))
            break

    # Вычетов на детей
    for line in lines:
        m = re.search(r"Вычетов на детей:\s*([\d\s]+,\d{2})", line)
        if m:
            slip.child_deductions = _parse_amount(m.group(1))
            break

    # Таблица начислений, удержаний и выплат
    earnings, deductions, payments = _parse_earnings_and_deductions(lines)
    slip.earnings = earnings
    slip.deductions = deductions
    slip.payments = payments

    return slip