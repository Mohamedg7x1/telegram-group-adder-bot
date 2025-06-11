import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackContext,
    ConversationHandler,
    filters
)
from telegram.error import TelegramError, BadRequest
import time
import random
import re

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
PHONE, FILE, CONFIRM = range(3)

class GroupAdderBot:
    def __init__(self):
        self.admin_phone = None
        self.usernames = []
        self.current_chat_id = None
        self.last_addition_time = 0
        self.additions_count = 0
        self.reset_time = time.time()
        
        # Safety limits
        self.MAX_ADDITIONS_PER_HOUR = 20
        self.MIN_DELAY_SECONDS = 15
        self.MAX_DELAY_SECONDS = 45
        self.MAX_DAILY_ADDITIONS = 100

    async def start(self, update: Update, context: CallbackContext) -> int:
        """Start the conversation."""
        await update.message.reply_text(
            "👋 Welcome to Group Adder Bot!\n\n"
            "Please send your admin phone number (with country code, e.g., +1234567890) for verification."
        )
        return PHONE

    async def verify_phone(self, update: Update, context: CallbackContext) -> int:
        """Verify admin phone number."""
        phone = update.message.text.strip()
        
        if not re.match(r'^\+\d{10,15}$', phone):
            await update.message.reply_text("❌ Invalid format. Please use +1234567890 format.")
            return PHONE
        
        self.admin_phone = phone
        await update.message.reply_text(
            f"✅ Verified: {phone}\n\n"
            "Now send a .txt file with usernames (one per line)."
        )
        return FILE

    async def process_file(self, update: Update, context: CallbackContext) -> int:
        """Process the username file."""
        if not update.message.document:
            await update.message.reply_text("Please upload a .txt file.")
            return FILE
            
        file = await update.message.document.get_file()
        if not file.file_name.lower().endswith('.txt'):
            await update.message.reply_text("Only .txt files accepted.")
            return FILE
            
        try:
            content = (await file.download_as_bytearray()).decode('utf-8')
            self.usernames = []
            invalid = []
            
            for line in content.split('\n'):
                username = line.strip().lstrip('@')
                if re.match(r'^[a-zA-Z0-9_]{5,32}$', username):
                    self.usernames.append(username)
                else:
                    invalid.append(line.strip())
            
            if not self.usernames:
                await update.message.reply_text("❌ No valid usernames found.")
                return FILE
                
            msg = (
                f"📊 Processed:\n"
                f"• Valid: {len(self.usernames)}\n"
                f"• Invalid: {len(invalid)}\n\n"
                "Now send the group chat ID or forward a group message."
            )
            
            if invalid:
                msg += f"\n\nInvalid entries:\n" + "\n".join(invalid[:5])
                if len(invalid) > 5:
                    msg += f"\n(and {len(invalid)-5} more...)"
            
            await update.message.reply_text(msg)
            return CONFIRM
            
        except Exception as e:
            logger.error(f"File error: {e}")
            await update.message.reply_text("❌ Error processing file.")
            return FILE

    async def confirm_and_add(self, update: Update, context: CallbackContext) -> int:
        """Add users to group."""
        try:
            chat_id = (update.message.forward_from_chat.id 
                      if update.message.forward_from_chat 
                      else int(update.message.text.strip()))
            
            bot = context.bot
            try:
                chat_member = await bot.get_chat_member(chat_id, bot.id)
                if not (chat_member.status == 'administrator' and chat_member.can_invite_users):
                    await update.message.reply_text("❌ Bot needs admin privileges.")
                    return CONFIRM
            except TelegramError as e:
                await update.message.reply_text(f"❌ Error: {e}")
                return CONFIRM
            
            await update.message.reply_text(
                f"⏳ Adding {len(self.usernames)} users...\n"
                f"Estimated time: {len(self.usernames) * 20 / 60:.1f} minutes"
            )
            
            added = []
            failed = []
            
            for username in self.usernames:
                if not await self.check_rate_limit():
                    failed.append(f"{username} (rate limit)")
                    continue
                
                try:
                    user = await bot.get_chat(f"@{username}")
                    if user.type != 'private':
                        failed.append(f"{username} (not user)")
                        continue
                        
                    delay = random.uniform(self.MIN_DELAY_SECONDS, self.MAX_DELAY_SECONDS)
                    time.sleep(delay)
                    
                    await bot.add_chat_member(chat_id, user.id)
                    self.additions_count += 1
                    self.last_addition_time = time.time()
                    added.append(username)
                    
                except TelegramError as e:
                    err = str(e).lower()
                    if "already" in err:
                        added.append(f"{username} (exists)")
                    elif "privacy" in err:
                        failed.append(f"{username} (privacy)")
                    elif "flood" in err:
                        failed.append(f"{username} (flood)")
                        time.sleep(60)
                    else:
                        failed.append(f"{username} (error)")
            
            # Send report
            report = (
                f"✅ Added: {len(added)}\n"
                f"❌ Failed: {len(failed)}\n\n"
                f"First 5 added:\n" + "\n".join(added[:5]) + 
                f"\n\nFirst 5 failed:\n" + "\n".join(failed[:5])
            )
            await update.message.reply_text(report)
            
            return ConversationHandler.END
            
        except Exception as e:
            logger.error(f"Error: {e}")
            await update.message.reply_text(f"❌ Failed: {str(e)[:200]}")
            return ConversationHandler.END

    async def check_rate_limit(self) -> bool:
        """Check rate limits."""
        now = time.time()
        if now - self.reset_time > 3600:
            self.additions_count = 0
            self.reset_time = now
        return self.additions_count < min(self.MAX_ADDITIONS_PER_HOUR, self.MAX_DAILY_ADDITIONS)

    async def cancel(self, update: Update, context: CallbackContext) -> int:
        """Cancel operation."""
        await update.message.reply_text("🚫 Operation cancelled.")
        return ConversationHandler.END

def main() -> None:
    """Run the bot."""
    application = Application.builder().token("7736244152:AAFk_42iceNa-cvvYw_eoAsKW7ckBHNgZfo").build()
    
    bot = GroupAdderBot()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", bot.start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.verify_phone)],
            FILE: [MessageHandler(filters.Document.FileExtension("txt"), bot.process_file)],
            CONFIRM: [MessageHandler(filters.TEXT | filters.FORWARDED, bot.confirm_and_add)],
        },
        fallbacks=[CommandHandler("cancel", bot.cancel)],
    )
    
    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == "__main__":
    main()