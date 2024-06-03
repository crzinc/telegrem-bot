import telebot
from telebot import types
import sqlite3
from datetime import datetime, timedelta
import stripe
import schedule
import threading
import time
from flask import Flask, request

# Telegram bot API token
API_TOKEN = 'YOUR_TELEGRAM_BOT_API_TOKEN'
bot = telebot.TeleBot(API_TOKEN)

# Stripe API key
stripe.api_key = 'YOUR_STRIPE_SECRET_KEY'

# Flask app for Stripe webhook
app = Flask(__name__)

# Database setup
conn = sqlite3.connect('subscriptions.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY,
    subscription_end DATE,
    trial_end DATE
)
''')
conn.commit()


def check_subscription(user_id):
    cursor.execute("SELECT subscription_end, trial_end FROM subscriptions WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result:
        subscription_end, trial_end = result
        return subscription_end, trial_end
    return None, None


def update_subscription(user_id, subscription_end=None, trial_end=None):
    if check_subscription(user_id) == (None, None):
        cursor.execute("INSERT INTO subscriptions (user_id, subscription_end, trial_end) VALUES (?, ?, ?)",
                       (user_id, subscription_end, trial_end))
    else:
        cursor.execute("UPDATE subscriptions SET subscription_end = ?, trial_end = ? WHERE user_id = ?",
                       (subscription_end, trial_end, user_id))
    conn.commit()


@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    subscription_end, trial_end = check_subscription(user_id)

    if subscription_end and subscription_end > datetime.now().date():
        bot.send_message(user_id, "Ваш платный доступ активен до " + subscription_end.strftime("%Y-%m-%d"))
    elif trial_end and trial_end > datetime.now().date():
        bot.send_message(user_id, "Ваш пробный доступ активен до " + trial_end.strftime("%Y-%m-%d"))
    else:
        bot.send_message(user_id, "Добро пожаловать! У вас нет активной подписки.")

    show_main_menu(message)


def show_main_menu(message):
    markup = types.ReplyKeyboardMarkup(row_width=2)
    info_btn = types.KeyboardButton('Информационное меню')
    subscription_btn = types.KeyboardButton('Управление подпиской')
    trial_btn = types.KeyboardButton('Пробный доступ')
    markup.add(info_btn, subscription_btn, trial_btn)
    bot.send_message(message.chat.id, "Выберите опцию:", reply_markup=markup)


@bot.message_handler(func=lambda message: message.text == 'Информационное меню')
def info_menu(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(text="О боте", callback_data="about_bot"))
    markup.add(types.InlineKeyboardButton(text="Как оформить подписку", callback_data="how_to_subscribe"))
    markup.add(types.InlineKeyboardButton(text="Поддержка", callback_data="support"))
    bot.send_message(message.chat.id, "Выберите раздел:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "about_bot")
def about_bot(call):
    bot.send_message(call.message.chat.id,
                     "Этот бот помогает управлять платными подписками и предоставляет пробный доступ.")


@bot.callback_query_handler(func=lambda call: call.data == "how_to_subscribe")
def how_to_subscribe(call):
    bot.send_message(call.message.chat.id,
                     "Для оформления подписки воспользуйтесь кнопкой 'Оплатить подписку' в разделе 'Управление подпиской'.")


@bot.callback_query_handler(func=lambda call: call.data == "support")
def support(call):
    bot.send_message(call.message.chat.id,
                     "Если у вас возникли вопросы, свяжитесь с поддержкой: support@yourdomain.com.")


def get_payment_link(user_id):
    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{
            'price_data': {
                'currency': 'usd',
                'product_data': {
                    'name': 'Subscription',
                },
                'unit_amount': 1000,
            },
            'quantity': 1,
        }],
        mode='payment',
        success_url='https://your-domain.com/success',
        cancel_url='https://your-domain.com/cancel',
        client_reference_id=user_id
    )
    return session.url


@bot.message_handler(func=lambda message: message.text == 'Управление подпиской')
def manage_subscription(message):
    user_id = message.from_user.id
    subscription_end, _ = check_subscription(user_id)

    if subscription_end and subscription_end > datetime.now().date():
        bot.send_message(message.chat.id, "Ваш платный доступ активен до " + subscription_end.strftime("%Y-%m-%d"))
    else:
        payment_link = get_payment_link(user_id)
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton(text="Оплатить подписку", url=payment_link)
        markup.add(btn)
        bot.send_message(message.chat.id, "У вас нет активной подписки. Пожалуйста, оплатите подписку.",
                         reply_markup=markup)


@bot.message_handler(func=lambda message: message.text == 'Пробный доступ')
def trial_access(message):
    user_id = message.from_user.id
    _, trial_end = check_subscription(user_id)

    if trial_end and trial_end > datetime.now().date():
        bot.send_message(message.chat.id, "Ваш пробный доступ активен до " + trial_end.strftime("%Y-%m-%d"))
    else:
        new_trial_end = datetime.now().date() + timedelta(days=7)  # Предоставляем 7 дней пробного доступа
        update_subscription(user_id, trial_end=new_trial_end)
        bot.send_message(message.chat.id,
                         "Вам предоставлен пробный доступ на 7 дней до " + new_trial_end.strftime("%Y-%m-%d"))


def check_subscriptions():
    cursor.execute("SELECT user_id, subscription_end FROM subscriptions")
    rows = cursor.fetchall()
    for row in rows:
        user_id, subscription_end = row
        if subscription_end and subscription_end - timedelta(days=3) == datetime.now().date():
            bot.send_message(user_id, "Ваш платный доступ истекает через 3 дня. Пожалуйста, продлите подписку.")
        elif subscription_end and subscription_end < datetime.now().date():
            bot.send_message(user_id, "Ваш платный доступ истек. Пожалуйста, продлите подписку.")


def schedule_checker():
    while True:
        schedule.run_pending()
        time.sleep(1)


schedule.every().day.at("09:00").do(check_subscriptions)
threading.Thread(target=schedule_checker).start()


@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    event = None

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, 'YOUR_STRIPE_ENDPOINT_SECRET'
        )
    except ValueError as e:
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError as e:
        return 'Invalid signature', 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        handle_checkout_session(session)

    return 'Success', 200


def handle_checkout_session(session):
    user_id = session['client_reference_id']
    subscription_end = datetime.now().date() + timedelta(days=30)  # Пример: 30 дней подписки
    update_subscription(user_id, subscription_end=subscription_end)


if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(port=4242)).start()
    bot.polling()
