import imaplib
import email
import os
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
import base64
import shutil

from pdf_parser import parse_payslip
from database import get_connection, init_db, insert_payslip

# ── Настройки ──────────────────────────────────────────────────────────
IMAP_SERVER = "imap.yandex.ru"
IMAP_PORT = 993
EMAIL_ADDR = "kadyrovas@yandex.ru"
APP_PASSWORD = os.environ.get("YANDEX_APP_PASSWORD")


FOLDERS = ["ПИЯФ|РСЧ"]

SENDER = "r_list@pnpi.nrcki.ru"
SUBJECT_PREFIX = "Расчетный листок ПИЯФ за"

SAVE_DIR = Path("downloads")


def imap_utf7_encode(folder: str) -> str:
    """
    Кодирует имя папки IMAP в формат UTF-7 (требование протокола IMAP
    для папок с нелатинскими символами).
    """
    result = []
    buf = bytearray()
    for ch in folder:
        code = ord(ch)
        if 0x20 <= code <= 0x7E:
            if buf:
                b64 = base64.b64encode(bytes(buf)).decode("ascii").rstrip("=")
                result.append("&" + b64 + "-")
                buf = bytearray()
            result.append("&-" if ch == "&" else ch)
        else:
            buf += ch.encode("utf-16-be")
    if buf:
        b64 = base64.b64encode(bytes(buf)).decode("ascii").rstrip("=")
        result.append("&" + b64 + "-")
    return "".join(result)


def decode_str(value: str) -> str:
    """
    Декодирует строку из заголовков письма (Subject, filename и т.д.),
    учитывая возможные кодировки (RFC 2047).
    """
    if value is None:
        return ""
    parts = decode_header(value)
    decoded = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            decoded.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(chunk)
    return "".join(decoded)


def process_folder(mail, folder: str, conn) -> int:
    """
    Открывает указанную папку IMAP, перебирает все письма,
    фильтрует по отправителю и теме, извлекает PDF-вложения,
    сохраняет их в SAVE_DIR, парсит содержимое и добавляет запись в БД.
    Возвращает количество новых записей, добавленных в БД.
    """
    encoded = imap_utf7_encode(folder)
    status, data = mail.select(f'"{encoded}"')
    if status != "OK":
        print(f"  Не удалось открыть папку: {data}")
        return 0

    n = int(data[0])
    print(f"  Писем в папке: {n}")
    if n == 0:
        return 0

    # Вместо серверного поиска — перебираем все письма
    status, data = mail.fetch("1:{}".format(n), "(RFC822)")
    if status != "OK":
        print(f"  Ошибка получения писем: {data}")
        return 0

    inserted_count = 0
    saved_count = 0
    skipped_count = 0

    for msg_data in data:
        # Каждая запись — кортеж или байты; пропускаем разделители
        if isinstance(msg_data, tuple):
            msg = email.message_from_bytes(msg_data[1])

            # Фильтр по отправителю
            from_header = msg.get("From", "")
            if SENDER not in from_header:
                skipped_count += 1
                continue

            # Фильтр по теме
            subject = decode_str(msg.get("Subject", ""))
            if not subject.startswith(SUBJECT_PREFIX):
                skipped_count += 1
                continue

            date_str = msg.get("Date", "")

            try:
                dt = parsedate_to_datetime(date_str)
                date_prefix = dt.strftime("%Y-%m-%d")
            except Exception:
                date_prefix = "unknown"

            for part in msg.walk():
                filename = decode_str(part.get_filename())
                ct = part.get_content_type()
                if not filename:
                    continue
                if ct == "application/pdf" or filename.lower().endswith(".pdf"):
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue

                    out_path = SAVE_DIR / f"{date_prefix}.pdf"
                    counter = 1
                    while out_path.exists():
                        out_path = SAVE_DIR / f"{date_prefix}_{counter}.pdf"
                        counter += 1
                    out_path.write_bytes(payload)
                    saved_count += 1

                    try:
                        payslip = parse_payslip(payload)
                        inserted = insert_payslip(conn, payslip)
                        if inserted:
                            inserted_count += 1
                    except Exception as e:
                        print(f"  ⚠ Ошибка при разборе {out_path.name}: {e}")

                    break

    print(f"  Пропущено (не по теме/отправителю): {skipped_count}")
    print(f"  Сохранено файлов: {saved_count}")
    print(f"  Добавлено записей в БД: {inserted_count}")
    return inserted_count


def main():
    """
    Точка входа: подключается к Яндекс Почте, инициализирует базу данных,
    очищает папку downloads, обрабатывает все указанные папки IMAP
    и выводит итог.
    """
    print("Подключение к Яндекс Почте…")
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(EMAIL_ADDR, APP_PASSWORD)
    print(f"Успешный вход: {EMAIL_ADDR}\n")

    # Инициализируем базу данных
    conn = get_connection()
    init_db(conn)

    # Очищаем папку downloads перед запуском
    if SAVE_DIR.exists():
        shutil.rmtree(SAVE_DIR)
    SAVE_DIR.mkdir(exist_ok=True)

    total = 0
    for folder in FOLDERS:
        print(f"Обработка папки «{folder}»…")
        total += process_folder(mail, folder, conn)
        print()

    conn.close()
    mail.logout()
    print(f"Готово. Добавлено новых записей в БД: {total}")


if __name__ == "__main__":
    main()
