import random
import logging
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pymongo import MongoClient
from pymongo.errors import ConnectionError
import uuid

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Connect to MongoDB with error handling
try:
    mongo_client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017'), serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')  # Test connection
    db = mongo_client['oak_forest_game']
    users_collection = db['users']
except ConnectionError as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise SystemExit("Cannot connect to MongoDB. Check MONGODB_URI.")

# Bot token from environment variable
TOKEN = os.getenv('TELEGRAM_TOKEN')
if not TOKEN:
    logger.error("TELEGRAM_TOKEN not set in environment variables.")
    raise SystemExit("TELEGRAM_TOKEN is required.")

# Improved time cycle (4 hours real-time = 1 game day)
def get_game_time():
    now = datetime.utcnow()
    cycle_duration = 4 * 3600  # 4 hours for a full cycle
    elapsed_seconds = (now - datetime(1970, 1, 1)).total_seconds()
    game_day = int((elapsed_seconds // (cycle_duration / 13)) % 13) + 1
    is_night = game_day > 6
    time_to_next = cycle_duration - (elapsed_seconds % cycle_duration)
    return game_day, is_night, timedelta(seconds=time_to_next)

# Update energy
def update_energy(user_data):
    now = datetime.utcnow()
    last_energy_update = user_data.get('last_energy_update', now)
    elapsed_hours = (now - last_energy_update).total_seconds() / 3600
    new_energy = min(user_data.get('energy', 10) + int(elapsed_hours * 2), 10)  # 2 energy per hour
    return new_energy

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    now = datetime.utcnow()
    users_collection.update_one(
        {'user_id': user.id},
        {
            '$setOnInsert': {
                'user_id': user.id,
                'username': user.username,
                'acorns': 0,
                'stars': 0,
                'squirrel_status': 'healthy',
                'squirrel_recovery_time': None,
                'level': 1,
                'trees': ['oak_1'],
                'squirrels': ['squirrel_1'],
                'energy': 10,
                'last_energy_update': now
            }
        },
        upsert=True
    )
    keyboard = [
        [InlineKeyboardButton("Start Exploring 🌳", callback_data='explore')],
        [InlineKeyboardButton("Collect Stars 🌟", callback_data='collect_star')],
        [InlineKeyboardButton("My Status 📊", callback_data='status')],
        [InlineKeyboardButton("Help ❓", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    game_day, is_night, _ = get_game_time()
    await update.message.reply_text(
        f"Welcome to the Zagros Oak Forest, {user.first_name}! 🌳\n"
        f"You own a mighty oak tree and a cute squirrel. Collect acorns, gather stars, and beware of threats! 😺\n"
        f"It's currently {'night' if is_night else 'day'} (Day {game_day}/13). You can collect stars at night!",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌳 Zagros Oak Forest Guide 🌳\n"
        "- Explore (🌳) to collect acorns. Each exploration costs 1 energy.\n"
        "- At night (after Day 6), collect stars (🌟) to increase your acorn chances.\n"
        "- Watch out for foxes, eagles, and storms! If your squirrel gets injured, it needs 2 hours to recover.\n"
        "- Level up every 50 acorns and get rewards!\n"
        "- Energy regenerates by 2 per hour (max 10).\n"
        "Commands: /start (start), /help (guide)"
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_data = users_collection.find_one({'user_id': user.id})
    
    if not user_data:
        await query.message.reply_text("Error: Please start the game again with /start!")
        return

    # Update energy
    new_energy = update_energy(user_data)
    users_collection.update_one(
        {'user_id': user.id},
        {'$set': {'energy': new_energy, 'last_energy_update': datetime.utcnow()}}
    )
    user_data['energy'] = new_energy

    # Check squirrel status
    if user_data['squirrel_status'] == 'injured':
        recovery_time = user_data['squirrel_recovery_time']
        if recovery_time and datetime.utcnow() < recovery_time:
            remaining = (recovery_time - datetime.utcnow()).total_seconds() / 60
            await query.message.reply_text(
                f"Your squirrel is injured and needs rest! 🩹 Come back in {int(remaining)} minutes."
            )
            return
        else:
            users_collection.update_one(
                {'user_id': user.id},
                {'$set': {'squirrel_status': 'healthy', 'squirrel_recovery_time': None}}
            )
            user_data['squirrel_status'] = 'healthy'

    game_day, is_night, time_to_next = get_game_time()

    if query.data == 'explore':
        if user_data['energy'] < 1:
            await query.message.reply_text("Your squirrel is tired! 😴 Wait for energy to recharge.")
            return
        keyboard = [
            [InlineKeyboardButton("North of the Tree 🌿", callback_data='explore_north')],
            [InlineKeyboardButton("South of the Tree 🍂", callback_data='explore_south')],
            [InlineKeyboardButton("Underground 🕳️", callback_data='explore_underground')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            f"Where do you want to explore? Energy: {user_data['energy']}/10 🐿️",
            reply_markup=reply_markup
        )

    elif query.data.startswith('explore_'):
        if user_data['energy'] < 1:
            await query.message.reply_text("Your squirrel is tired! 😴 Wait for energy to recharge.")
            return
        # Deduct energy
        users_collection.update_one(
            {'user_id': user.id},
            {'$inc': {'energy': -1}}
        )
        # Calculate acorns (stars increase chances)
        star_bonus = 2 if user_data['stars'] > 0 else 1
        acorns_found = random.randint(0, 5) * star_bonus
        # Diverse threats
        threats = [
            ("fox", 0.1),
            ("eagle", 0.05),
            ("storm", 0.05)
        ]
        threat = None
        for t_name, t_prob in threats:
            if random.random() < t_prob:
                threat = t_name
                break
        location = query.data.split('_')[1].capitalize()

        if threat:
            users_collection.update_one(
                {'user_id': user.id},
                {
                    '$set': {
                        'squirrel_status': 'injured',
                        'squirrel_recovery_time': datetime.utcnow() + timedelta(hours=2)
                    }
                }
            )
            await query.message.reply_text(
                f"Oh no! A {threat} injured your squirrel! 😿 It can't explore for 2 hours."
            )
        else:
            users_collection.update_one(
                {'user_id': user.id},
                {'$inc': {'acorns': acorns_found}}
            )
            # Check for level up
            user_data = users_collection.find_one({'user_id': user.id})
            new_level = user_data['level']
            if user_data['acorns'] >= new_level * 50:
                new_level += 1
                users_collection.update_one(
                    {'user_id': user.id},
                    {'$set': {'level': new_level}, '$inc': {'stars': 1, 'energy': 5}}
                )
                await query.message.reply_text(
                    f"Your squirrel explored {location} and found {acorns_found} acorns! 🌰\n"
                    f"Congratulations! You leveled up to level {new_level}! 🎉 You got 1 star and 5 energy as a reward.\n"
                    "Explore again?",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("Explore Again 🌳", callback_data='explore')]
                    ])
                )
            else:
                await query.message.reply_text(
                    f"Your squirrel explored {location} and found {acorns_found} acorns! 🌰\n"
                    "Explore again?",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("Explore Again 🌳", callback_data='explore')]
                    ])
                )

    elif query.data == 'collect_star':
        if not is_night:
            hours_to_night = time_to_next.total_seconds() / 3600
            await query.message.reply_text(
                f"It's daytime! 🌞 Come back in {int(hours_to_night)} hours for night to collect stars."
            )
            return
        stars_available = random.randint(1, 3)
        keyboard = [
            [InlineKeyboardButton(f"Star {i+1} 🌟", callback_data=f'star_{i+1}')]
            for i in range(stars_available)
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            f"{stars_available} stars are shining in the sky! Which one do you want? ✨",
            reply_markup=reply_markup
        )

    elif query.data.startswith('star_'):
        users_collection.update_one(
            {'user_id': user.id},
            {'$inc': {'stars': 1}}
        )
        await query.message.reply_text(
            "You caught a star! ✨ Now you have a better chance of finding acorns!\n"
            "Hurry, go explore!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Explore 🌳", callback_data='explore')]
            ])
        )

    elif query.data == 'status':
        user_data = users_collection.find_one({'user_id': user.id})
        next_level_acorns = user_data['level'] * 50
        await query.message.reply_text(
            f"📊 Your Status:\n"
            f"Acorns: {user_data['acorns']}/{next_level_acorns} 🌰\n"
            f"Stars: {user_data['stars']} ✨\n"
            f"Level: {user_data['level']} 🎖️\n"
            f"Energy: {user_data['energy']}/10 ⚡\n"
            f"Squirrel Status: {'Healthy' if user_data['squirrel_status'] == 'healthy' else 'Injured'} 🐿️\n"
            f"Trees: {len(user_data['trees'])} 🌳\n"
            f"Game Time: {'Night' if is_night else 'Day'} (Day {game_day}/13)\n"
            f"Time to next day/night: {int(time_to_next.total_seconds() / 3600)} hours"
        )

    elif query.data == 'help':
        await query.message.reply_text(
            "🌳 Zagros Oak Forest Guide 🌳\n"
            "- Explore (🌳) to collect acorns. Each exploration costs 1 energy.\n"
            "- At night (after Day 6), collect stars (🌟) to increase your acorn chances.\n"
            "- Watch out for foxes, eagles, and storms! If your squirrel gets injured, it needs 2 hours to recover.\n"
            "- Level up every 50 acorns and get rewards!\n"
            "- Energy regenerates by 2 per hour (max 10).\n"
            "Commands: /start (start), /help (guide)"
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    await update.message.reply_text("An error occurred! Please try again or start over with /start.")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button))
    app.add_error_handler(error_handler)
    
    logger.info("Starting bot...")
    app.run_polling()

if __name__ == '__main__':
    main()