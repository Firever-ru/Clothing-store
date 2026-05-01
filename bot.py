# bot.py
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
)
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from config import *

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

AUTH_ADMINS = {}

def authorize_admin(user_id: int):
    AUTH_ADMINS[user_id] = datetime.now() + timedelta(minutes=ADMIN_SESSION_TTL_MINUTES)

def is_admin_authorized(user_id: int) -> bool:
    exp = AUTH_ADMINS.get(user_id)
    return bool(exp and datetime.now() < exp)

# sql

def init_db():
    conn = sqlite3.connect("shop.db")
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo TEXT,
        description TEXT,
        price INTEGER,
        sizes TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        product_id INTEGER,
        size TEXT,
        status TEXT,
        proof TEXT,
        address TEXT,
        track TEXT,
        received_date TEXT,
        delivery TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# menu

def get_main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Каталог одежды"))
    kb.add(KeyboardButton("Мои заказы"))
    kb.add(KeyboardButton("Связь с поддержкой"))
    return kb

def get_admin_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Выложить одежду"))
    kb.add(KeyboardButton("Действующий каталог"))
    kb.add(KeyboardButton("Заказы"))
    kb.add(KeyboardButton("Оповестить клиентов"))
    kb.add(KeyboardButton("Очистить историю заказов"))
    return kb

# fsm

class AddProduct(StatesGroup):
    photo = State()
    price = State()
    sizes = State()

class EditProduct(StatesGroup):
    desc = State()
    price = State()
    sizes = State()

class Broadcast(StatesGroup):
    text = State()

class AddressInput(StatesGroup):
    waiting_for_address = State()  

class TrackInput(StatesGroup):
    waiting_for_track = State()    

class AdminLogin(StatesGroup):
    waiting_for_password = State()

# db functions

def db_conn():
    return sqlite3.connect("shop.db")

def get_product_by_offset(offset: int):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, photo, description, price, sizes FROM catalog ORDER BY id LIMIT 1 OFFSET ?", (offset,))
    row = cur.fetchone()
    conn.close()
    return row

def count_products():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM catalog")
    total = cur.fetchone()[0]
    conn.close()
    return total

def normalize_sizes(text: str) -> str:
    t = text.replace(" ", "").upper()
    if "," in t:
        return t
    parts = []
    buf = ""
    for ch in t:
        buf += ch
        if len(buf) >= 2 and (buf.endswith("S") or buf.endswith("M") or buf.endswith("L") or buf.isdigit()):
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    return ",".join(parts) if parts else t

def main_menu_text() -> str:
    return "Добро пожаловать в магазин одежды\nВыберите действие ниже:"

def admin_only(message: types.Message) -> bool:
    return is_admin_authorized(message.from_user.id)

async def guard_admin_cb(call: types.CallbackQuery) -> bool:
    if not is_admin_authorized(call.from_user.id):
        await call.answer("Нет доступа. Введите /ap.", show_alert=True)
        return False
    return True

# catalog keyboard

def catalog_keyboard(page: int, total: int, product_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("⬅️", callback_data=f"cat_prev_{page}"),
        InlineKeyboardButton("➡️", callback_data=f"cat_next_{page}")
    )
    kb.add(InlineKeyboardButton("Заказать", callback_data=f"order_{product_id}"))
    return kb

def admin_catalog_keyboard(prod_id: int, page: int, total: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Изменить описание", callback_data=f"editdesc_{prod_id}"))
    kb.add(InlineKeyboardButton("Изменить цену", callback_data=f"editprice_{prod_id}"))
    kb.add(InlineKeyboardButton("Изменить размеры", callback_data=f"editsize_{prod_id}"))
    kb.add(InlineKeyboardButton("Удалить", callback_data=f"delask_{prod_id}"))
    kb.row(
        InlineKeyboardButton("⬅️", callback_data=f"aprev_{page}"),
        InlineKeyboardButton("➡️", callback_data=f"anext_{page}")
    )
    return kb

def order_admin_keyboard(order_id: int, status: str, username: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    if status == STATUS_PAID:
        kb.add(InlineKeyboardButton("Отправлен", callback_data=f"sent_{order_id}"))
    if status not in (STATUS_DECLINED, STATUS_RECEIVED, STATUS_CANCELLED):
        kb.add(InlineKeyboardButton("Отменить", callback_data=f"cancel_{order_id}"))
    kb.add(InlineKeyboardButton("Связаться", callback_data=f"contact_{order_id}"))
    return kb

# commands

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    await message.answer(main_menu_text(), reply_markup=get_main_menu(), parse_mode="Markdown")

@dp.message_handler(commands=["ap"])
async def cmd_admin(message: types.Message):
    await AdminLogin.waiting_for_password.set()
    await message.answer("Введите пароль админ‑панели:")

@dp.message_handler(state=AdminLogin.waiting_for_password, content_types=["text"])
async def admin_check_password(message: types.Message, state: FSMContext):
    if message.text.strip() == ADMIN_PASSWORD:
        authorize_admin(message.from_user.id)
        await state.finish()
        await message.answer("Доступ разрешён. Админ‑панель:", reply_markup=get_admin_menu())
    else:
        await state.finish()
        await message.answer("Неверный пароль.")

# menu handlers

@dp.message_handler(lambda m: m.text == "Связь с поддержкой")
async def client_support(message: types.Message):
    await message.answer(f"Связь с владельцем: {SUPPORT_USERNAME}")

# add product

@dp.message_handler(lambda m: m.text == "Выложить одежду")
async def add_product_start(message: types.Message):
    if not admin_only(message):
        return
    await message.answer("Отправьте *фото* товара с подписью (название/описание).", parse_mode="Markdown")
    await AddProduct.photo.set()

@dp.message_handler(content_types=["photo"], state=AddProduct.photo)
async def add_product_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    description = message.caption if message.caption else "Без описания"
    await state.update_data(photo=photo_id, description=description)
    await message.answer("Введите цену (только число):")
    await AddProduct.price.set()

@dp.message_handler(lambda m: m.text.isdigit(), state=AddProduct.price)
async def add_product_price(message: types.Message, state: FSMContext):
    await state.update_data(price=int(message.text))
    await message.answer("Введите размеры *без пробелов*, через запятую (например: `S,M,L,XL`):", parse_mode="Markdown")
    await AddProduct.sizes.set()

@dp.message_handler(state=AddProduct.sizes)
async def add_product_sizes(message: types.Message, state: FSMContext):
    data = await state.get_data()
    sizes = normalize_sizes(message.text)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO catalog (photo, description, price, sizes) VALUES (?, ?, ?, ?)",
        (data["photo"], data["description"], data["price"], sizes)
    )
    conn.commit()
    conn.close()
    await state.finish()
    await message.answer("Товар добавлен в каталог.", reply_markup=get_admin_menu())

# catalog

@dp.message_handler(lambda m: m.text == "Каталог одежды")
async def show_catalog(message: types.Message):
    total = count_products()
    if total == 0:
        await message.answer("Каталог пуст. Загляните позже.")
        return
    row = get_product_by_offset(0)
    prod_id, photo, desc, price, sizes = row
    await message.answer_photo(
        photo,
        caption=f"{desc}\n\nЦена: {price} руб.\nРазмеры: {sizes}",
        reply_markup=catalog_keyboard(0, total, prod_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("cat_prev_") or c.data.startswith("cat_next_"))
async def catalog_paginate(call: types.CallbackQuery):
    parts = call.data.split("_")
    direction, current_page = parts[1], int(parts[2])
    total = count_products()
    if total == 0:
        await call.answer("Каталог пуст.")
        return
    if direction == "prev":
        new_page = max(0, current_page - 1)
    else:
        new_page = min(total - 1, current_page + 1)
    row = get_product_by_offset(new_page)
    prod_id, photo, desc, price, sizes = row
    try:
        await call.message.edit_media(
            InputMediaPhoto(photo, caption=f"{desc}\n\nЦена: {price} руб.\nРазмеры: {sizes}")
        )
        await call.message.edit_reply_markup(reply_markup=catalog_keyboard(new_page, total, prod_id))
    except Exception:
        await call.message.answer_photo(
            photo,
            caption=f"{desc}\n\nЦена: {price} руб.\nРазмеры: {sizes}",
            reply_markup=catalog_keyboard(new_page, total, prod_id)
        )
    await call.answer()

# order

@dp.callback_query_handler(lambda c: c.data.startswith("order_"))
async def order_pick_size(call: types.CallbackQuery):
    product_id = int(call.data.split("_")[1])
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT sizes, price, description FROM catalog WHERE id=?", (product_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        await call.answer("Товар не найден.", show_alert=True)
        return
    sizes_str, price, desc = row
    sizes = [s.strip() for s in sizes_str.split(",") if s.strip()]
    kb = InlineKeyboardMarkup()
    for s in sizes:
        kb.add(InlineKeyboardButton(s, callback_data=f"choose_{product_id}_{s}"))
    await call.message.answer(
        f"{desc}\n"
        f"Цена: {price} руб. (+ стоимость доставки, с вами свяжется менеджер в момент отправки)\n\n"
        f"Выберите размер:",
        reply_markup=kb
    )
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("choose_"))
async def choose_size(call: types.CallbackQuery):
    _, product_id, size = call.data.split("_")
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT price, description FROM catalog WHERE id=?", (product_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await call.answer("Товар не найден.", show_alert=True)
        return
    price, desc = row

    cur.execute(
        "INSERT INTO orders (user_id, username, product_id, size, status) VALUES (?, ?, ?, ?, ?)",
        (call.from_user.id, call.from_user.username, int(product_id), size, STATUS_WAIT)
    )
    order_id = cur.lastrowid
    conn.commit()
    conn.close()

    kb_deliv = InlineKeyboardMarkup().row(
        InlineKeyboardButton("Почта России", callback_data=f"delivery_{order_id}_POST"),
        InlineKeyboardButton("CDEK (дороже)", callback_data=f"delivery_{order_id}_CDEK")
    )

    await call.message.answer(
        f"Заказ №{order_id}\n"
        f"Товар: {desc}\nРазмер: {size}\n"
        f"Сумма к оплате: {price} руб. (+ стоимость доставки, с вами свяжется менеджер в момент отправки)\n\n"
        f"Оплатите по реквизитам:\n"
        f"Карта: {CARD_NUMBER}\n"
        f"Держатель: {CARD_NAME}\n\n"
        f"После оплаты отправьте *СКРИНШОТ перевода* и *имя отправителя* одним сообщением (скриншот + подпись).\n\n"
        f"Выберите способ получения:",
        reply_markup=kb_deliv,
        parse_mode="Markdown"
    )
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("delivery_"))
async def set_delivery(call: types.CallbackQuery):
    _, order_id_str, method = call.data.split("_")
    order_id = int(order_id_str)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, status FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await call.answer("Заказ не найден.", show_alert=True)
        return
    user_id, status = row
    if call.from_user.id != user_id:
        conn.close()
        await call.answer("Доступ запрещён.", show_alert=True)
        return

    cur.execute("UPDATE orders SET delivery=? WHERE id=?", (method, order_id))
    conn.commit()
    conn.close()

    human = "Почта России" if method == "POST" else "CDEK (дороже)"
    await call.answer(f"Выбрано: {human}")

    if status == STATUS_PAID:
        client_state = dp.current_state(chat=user_id, user=user_id)
        await client_state.set_state(AddressInput.waiting_for_address.state)
        await client_state.update_data(order_id=order_id)
        if method == "CDEK":
            await bot.send_message(user_id, "Пожалуйста, отправьте *полный адрес отделения CDEK* (город, улица, дом) одним сообщением.", parse_mode="Markdown")
        else:
            await bot.send_message(user_id, "Пожалуйста, отправьте *ФИО, индекс и полный почтовый адрес* для доставки Почтой России одним сообщением.", parse_mode="Markdown")

@dp.message_handler(content_types=["photo"])
async def receive_payment_proof(message: types.Message):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM orders WHERE user_id=? AND status=? ORDER BY id DESC LIMIT 1",
        (message.from_user.id, STATUS_WAIT)
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return
    order_id = row[0]

    proof_caption = message.caption if message.caption else "Без подписи"
    cur.execute("UPDATE orders SET proof=? WHERE id=?", (proof_caption, order_id))
    conn.commit()
    conn.close()

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("Подтвердить оплату", callback_data=f"confirm_{order_id}"),
        InlineKeyboardButton("Отклонить", callback_data=f"decline_{order_id}")
    )
    await bot.send_photo(
        ADMIN_ID,
        message.photo[-1].file_id,
        caption=f"Новый платёж\nЗаказ №{order_id}\nОт @{message.from_user.username}\nКомментарий: {proof_caption}",
        reply_markup=kb
    )
    await message.answer("Чек отправлен владельцу. Ожидайте подтверждения.")

@dp.callback_query_handler(lambda c: c.data.startswith("confirm_") or c.data.startswith("decline_"))
async def admin_confirm_or_decline(call: types.CallbackQuery):
    if not await guard_admin_cb(call):
        return

    order_id = int(call.data.split("_")[1])

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await call.answer("Заказ не найден.", show_alert=True)
        return
    user_id = row[0]

    if call.data.startswith("decline_"):
        cur.execute("UPDATE orders SET status=? WHERE id=?", (STATUS_DECLINED, order_id))
        conn.commit()
        conn.close()
        await bot.send_message(user_id, "Оплата отклонена. Вы вернулись к главному меню.", reply_markup=get_main_menu())
        try:
            await call.message.edit_caption(call.message.caption + "\n\nОТКЛОНЕНО")
        except Exception:
            pass
        await call.answer("Оплата отклонена.")
        return

    cur.execute("UPDATE orders SET status=? WHERE id=?", (STATUS_PAID, order_id))
    cur.execute("SELECT delivery FROM orders WHERE id=?", (order_id,))
    drow = cur.fetchone()
    delivery = drow[0] if drow else None
    conn.commit()
    conn.close()

    if not delivery:
        kb_deliv = InlineKeyboardMarkup().row(
            InlineKeyboardButton("Почта России", callback_data=f"delivery_{order_id}_POST"),
            InlineKeyboardButton("CDEK (дороже)", callback_data=f"delivery_{order_id}_CDEK")
        )
        await bot.send_message(
            user_id,
            "Оплата подтверждена!\n\nВыберите, как получить заказ:",
            reply_markup=kb_deliv
        )
    else:
        client_state = dp.current_state(chat=user_id, user=user_id)
        await client_state.set_state(AddressInput.waiting_for_address.state)
        await client_state.update_data(order_id=order_id)
        if delivery == "CDEK":
            await bot.send_message(
                user_id,
                "Оплата подтверждена!\nТеперь, пожалуйста, отправьте *полный адрес отделения CDEK* (город, улица, дом) одним сообщением.",
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(
                user_id,
                "Оплата подтверждена!\nТеперь, пожалуйста, отправьте *ФИО, индекс и полный почтовый адрес* для доставки Почтой России одним сообщением.",
                parse_mode="Markdown"
            )

    try:
        await call.message.edit_caption(call.message.caption + "\n\n✅ ОПЛАЧЕНО")
    except Exception:
        pass
    await call.answer("Подтверждено.")

@dp.message_handler(state=AddressInput.waiting_for_address, content_types=["text"])
async def receive_address(message: types.Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await state.finish()
        return

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET address=? WHERE id=?", (message.text.strip(), order_id))
    cur.execute("SELECT delivery FROM orders WHERE id=?", (order_id,))
    drow = cur.fetchone()
    delivery = drow[0] if drow else None
    conn.commit()
    conn.close()

    await message.answer("Адрес сохранён. Как только заказ будет отправлен — мы пришлём трек-код.")
    await bot.send_message(ADMIN_ID, f"Заказ №{order_id}\nДоставка: {'CDEK' if delivery=='CDEK' else 'Почта России' if delivery=='POST' else 'не выбрано'}\nАдрес клиента: {message.text.strip()}")
    await state.finish()

# my orders

@dp.message_handler(lambda m: m.text == "Мои заказы")
async def my_orders(message: types.Message):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, product_id, size, status, track, delivery
        FROM orders
        WHERE user_id=?
        ORDER BY id DESC
    """, (message.from_user.id,))
    orders = cur.fetchall()
    conn.close()

    if not orders:
        await message.answer("У вас пока нет заказов.")
        return

    for order_id, product_id, size, status, track, delivery in orders:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute("SELECT description FROM catalog WHERE id=?", (product_id,))
        prod = cur.fetchone()
        conn.close()
        name = prod[0] if prod else f"Товар #{product_id}"

        delivery_name = "CDEK" if delivery == "CDEK" else "Почта России" if delivery == "POST" else "не выбран"
        text = (
            f"Заказ №{order_id}\n"
            f"{name}\n"
            f"Размер: {size}\n"
            f"Доставка: {delivery_name}\n"
            f"Статус: {status}"
        )
        if track:
            text += f"\nТрек-код: {track}"

        kb = None
        if status == STATUS_SENT:
            kb = InlineKeyboardMarkup().add(
                InlineKeyboardButton("Заказ получен", callback_data=f"received_{order_id}")
            )
        await message.answer(text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("received_"))
async def mark_order_received(call: types.CallbackQuery):
    order_id = int(call.data.split("_")[1])
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE orders SET status=?, received_date=? WHERE id=?",
        (STATUS_RECEIVED, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), order_id)
    )
    conn.commit()
    conn.close()
    await call.message.answer("Спасибо! Заказ отмечен как *получен*.", parse_mode="Markdown")
    try:
        await call.message.edit_reply_markup()
    except Exception:
        pass
    await call.answer()

# admin orders

@dp.message_handler(lambda m: m.text == "Заказы")
async def admin_orders(message: types.Message):
    if not admin_only(message):
        return

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, user_id, username, product_id, size, status
        FROM orders
        WHERE status IN (?,?)
        ORDER BY id DESC
        LIMIT ?
    """, (STATUS_PAID, STATUS_SENT, ADMIN_ORDERS_PAGE_SIZE))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await message.answer("Оплаченных/отправленных заказов нет.")
        return

    for order_id, user_id, username, product_id, size, status in rows:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute("SELECT description FROM catalog WHERE id=?", (product_id,))
        prod = cur.fetchone()
        conn.close()
        name = prod[0] if prod else f"Товар #{product_id}"
        text = f"🧾 Заказ №{order_id}\n👤 @{username}\n{name}\n📏 Размер: {size}\nСтатус: {status}"
        await message.answer(text, reply_markup=order_admin_keyboard(order_id, status, username))

@dp.callback_query_handler(lambda c: c.data.startswith("sent_"))
async def admin_sent(call: types.CallbackQuery, state: FSMContext):
    if not await guard_admin_cb(call):
        return
    order_id = int(call.data.split("_")[1])

    await TrackInput.waiting_for_track.set()
    await state.update_data(order_id=order_id)

    await call.message.answer(f"Введите *трек-код* для заказа №{order_id}:", parse_mode="Markdown")
    await call.answer()

@dp.message_handler(state=TrackInput.waiting_for_track, content_types=["text"])
async def admin_save_track(message: types.Message, state: FSMContext):
    if not admin_only(message):
        await state.finish()
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    track = message.text.strip()

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status=?, track=? WHERE id=?", (STATUS_SENT, track, order_id))
    cur.execute("SELECT user_id FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    conn.commit()
    conn.close()

    if row:
        user_id = row[0]
        await bot.send_message(user_id, f"Ваш заказ №{order_id} *отправлен*!\nТрек-код: `{track}`",
                               parse_mode="Markdown")

    await message.answer("Трек-код сохранён, клиент уведомлён.")
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("contact_"))
async def admin_contact(call: types.CallbackQuery):
    if not await guard_admin_cb(call):
        return
    order_id = int(call.data.split("_")[1])
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT username FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        await call.message.answer(f"Связаться с клиентом: @{row[0]}")
    else:
        await call.message.answer("У клиента нет username.")
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("cancel_"))
async def admin_cancel(call: types.CallbackQuery):
    if not await guard_admin_cb(call):
        return
    order_id = int(call.data.split("_")[1])

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status=? WHERE id=?", (STATUS_CANCELLED, order_id))
    cur.execute("SELECT user_id FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    conn.commit()
    conn.close()

    if row:
        await bot.send_message(row[0], f"Ваш заказ №{order_id} был *отменён* владельцем.", parse_mode="Markdown")
    await call.message.answer("Заказ отменён.")
    await call.answer()

# broadcast

@dp.message_handler(lambda m: m.text == "Оповестить клиентов")
async def start_broadcast(message: types.Message):
    if not admin_only(message):
        return
    await message.answer("Введите текст рассылки:")
    await Broadcast.text.set()

@dp.message_handler(state=Broadcast.text, content_types=["text"])
async def send_broadcast(message: types.Message, state: FSMContext):
    if not admin_only(message):
        await state.finish()
        return
    text = message.text
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT user_id FROM orders")
    users = [row[0] for row in cur.fetchall()]
    conn.close()
    sent = 0
    for uid in users:
        try:
            await bot.send_message(uid, f"Оповещение:\n\n{text}")
            sent += 1
        except Exception:
            pass
    await message.answer(f"Рассылка завершена. Отправлено: {sent}.")
    await state.finish()

# clear history

@dp.message_handler(lambda m: m.text == "Очистить историю заказов")
async def clear_history(message: types.Message):
    if not admin_only(message):
        return
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM orders WHERE status IN (?, ?, ?)", (STATUS_RECEIVED, STATUS_CANCELLED, STATUS_DECLINED))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    await message.answer(f"🗑 Удалено завершённых заказов: {deleted}")

# admin catalog

@dp.message_handler(lambda m: m.text == "Действующий каталог")
async def admin_catalog(message: types.Message):
    if not admin_only(message):
        return
    total = count_products()
    if total == 0:
        await message.answer("Каталог пуст.")
        return
    row = get_product_by_offset(0)
    prod_id, photo, desc, price, sizes = row
    await message.answer_photo(
        photo,
        caption=f"#{prod_id} {desc}\n{price} руб.\n{sizes}",
        reply_markup=admin_catalog_keyboard(prod_id, 0, total)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("aprev_") or c.data.startswith("anext_"))
async def admin_catalog_paginate(call: types.CallbackQuery):
    if not await guard_admin_cb(call):
        return
    total = count_products()
    if total == 0:
        await call.answer("Каталог пуст.")
        return
    parts = call.data.split("_")
    direction, current_page = parts[0], int(parts[1])
    if direction == "aprev":
        new_page = max(0, current_page - 1)
    else:
        new_page = min(total - 1, current_page + 1)
    row = get_product_by_offset(new_page)
    prod_id, photo, desc, price, sizes = row
    try:
        await call.message.edit_media(InputMediaPhoto(photo, caption=f"#{prod_id} {desc}\n{price} руб.\n{sizes}"))
        await call.message.edit_reply_markup(reply_markup=admin_catalog_keyboard(prod_id, new_page, total))
    except Exception:
        await call.message.answer_photo(
            photo,
            caption=f"#{prod_id} {desc}\n{price} руб.\n{sizes}",
            reply_markup=admin_catalog_keyboard(prod_id, new_page, total)
        )
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("editdesc_"))
async def edit_desc_start(call: types.CallbackQuery, state: FSMContext):
    if not await guard_admin_cb(call):
        return
    prod_id = int(call.data.split("_")[1])
    await state.update_data(prod_id=prod_id)
    await call.message.answer("✏ Введите новое описание:")
    await EditProduct.desc.set()
    await call.answer()

@dp.message_handler(state=EditProduct.desc, content_types=["text"])
async def edit_desc_save(message: types.Message, state: FSMContext):
    if not admin_only(message):
        await state.finish()
        return
    data = await state.get_data()
    prod_id = data["prod_id"]
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE catalog SET description=? WHERE id=?", (message.text, prod_id))
    conn.commit()
    conn.close()
    await state.finish()
    await message.answer("Описание обновлено.")

@dp.callback_query_handler(lambda c: c.data.startswith("editprice_"))
async def edit_price_start(call: types.CallbackQuery, state: FSMContext):
    if not await guard_admin_cb(call):
        return
    prod_id = int(call.data.split("_")[1])
    await state.update_data(prod_id=prod_id)
    await call.message.answer("Введите новую цену (только число):")
    await EditProduct.price.set()
    await call.answer()

@dp.message_handler(state=EditProduct.price, content_types=["text"])
async def edit_price_save(message: types.Message, state: FSMContext):
    if not admin_only(message):
        await state.finish()
        return
    if not message.text.isdigit():
        await message.answer("Введите число, например: 2499")
        return
    data = await state.get_data()
    prod_id = data["prod_id"]
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE catalog SET price=? WHERE id=?", (int(message.text), prod_id))
    conn.commit()
    conn.close()
    await state.finish()
    await message.answer("Цена обновлена.")

@dp.callback_query_handler(lambda c: c.data.startswith("editsize_"))
async def edit_sizes_start(call: types.CallbackQuery, state: FSMContext):
    if not await guard_admin_cb(call):
        return
    prod_id = int(call.data.split("_")[1])
    await state.update_data(prod_id=prod_id)
    await call.message.answer("Введите новые размеры через запятую (например: S,M,L,XL):")
    await EditProduct.sizes.set()
    await call.answer()

@dp.message_handler(state=EditProduct.sizes, content_types=["text"])
async def edit_sizes_save(message: types.Message, state: FSMContext):
    if not admin_only(message):
        await state.finish()
        return
    sizes = normalize_sizes(message.text)
    data = await state.get_data()
    prod_id = data["prod_id"]
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE catalog SET sizes=? WHERE id=?", (sizes, prod_id))
    conn.commit()
    conn.close()
    await state.finish()
    await message.answer("Размеры обновлены.")

@dp.callback_query_handler(lambda c: c.data.startswith("delask_"))
async def delete_ask(call: types.CallbackQuery):
    if not await guard_admin_cb(call):
        return
    prod_id = int(call.data.split("_")[1])
    kb = InlineKeyboardMarkup().row(
        InlineKeyboardButton("Да, удалить", callback_data=f"delyes_{prod_id}"),
        InlineKeyboardButton("Отмена", callback_data=f"delno_{prod_id}")
    )
    await call.message.answer(f"Вы уверены, что хотите удалить товар #{prod_id}?", reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("delyes_") or c.data.startswith("delno_"))
async def delete_do(call: types.CallbackQuery):
    if not await guard_admin_cb(call):
        return
    prod_id = int(call.data.split("_")[1])
    if call.data.startswith("delno_"):
        await call.message.answer("Удаление отменено.")
        await call.answer()
        return
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM catalog WHERE id=?", (prod_id,))
    conn.commit()
    conn.close()
    await call.message.answer(f"Товар #{prod_id} удалён.")
    await call.answer()

@dp.message_handler(commands=["sql"])
async def sql_command(message: types.Message):
    if not admin_only(message):
        return await message.answer("Нет доступа. Введите /ap.")
    query = message.get_args()
    if not query:
        return await message.answer("Использование: /sql SELECT * FROM orders;")
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(query)
        if query.strip().upper().startswith("SELECT"):
            rows = cur.fetchall()
            if not rows:
                await message.answer("Пусто.")
            else:
                text = "\n".join(str(r) for r in rows[:20])
                if len(rows) > 20:
                    text += f"\n… и ещё {len(rows) - 20} строк(и)"
                await message.answer(f"Результат:\n\n{text}")
        else:
            conn.commit()
            await message.answer("Запрос выполнен.")
        conn.close()
    except Exception as e:
        await message.answer(f"Ошибка SQL: {e}")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
