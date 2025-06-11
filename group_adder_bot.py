import logging
from telegram import Update, Bot, Message
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackContext,
    MessageHandler,
    Filters,
    ConversationHandler
)
from telegram.error import TelegramError, BadRequest
import time
import random
import re
from typing import Dict, List

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
        
        # Configuration
        self.MAX_ADDITIONS_PER_HOUR = 20
        self.MIN_DELAY_SECONDS = 15
        self.MAX_DELAY_SECONDS = 45
        self.MAX_DAILY_ADDITIONS = 100

    async def start(self, update: Update, context: CallbackContext) -> int:
        """Start the conversation and ask for admin phone number."""
        await update.message.reply_text(
            "👋 Welcome to Group Adder Bot!\n\n"
            "To start, please send me your admin phone number "
            "(including country code, e.g., +1234567890) for verification."
        )
        return PHONE

    async def verify_phone(self, update: Update, context: CallbackContext) -> int:
        """Verify admin phone number."""
        phone = update.message.text.strip()
        
        # Basic phone validation
        if not re.match(r'^\+\d{10,15}$', phone):
            await update.message.reply_text(
                "❌ Invalid phone format. Please send a valid phone number "
                "with country code (e.g., +1234567890)."
            )
            return PHONE
        
        self.admin_phone = phone
        self.current_chat_id = update.effective_chat.id
        
        await update.message.reply_text(
            f"✅ Phone number verified: {phone}\n\n"
            "Now please send me a .txt file containing the list of usernames "
            "(one username per line, with or without @)."
        )
        return FILE

    async def process_file(self, update: Update, context: CallbackContext) -> int:
        """Process the uploaded username file."""
        if not update.message.document:
            await update.message.reply_text("Please upload a .txt file.")
            return FILE
            
        file = await update.message.document.get_file()
        file_name = update.message.document.file_name
        
        if not file_name.lower().endswith('.txt'):
            await update.message.reply_text("Please upload a .txt file.")
            return FILE
            
        try:
            # Download and process the file
            file_content = (await file.download_as_bytearray()).decode('utf-8')
            raw_usernames = [line.strip() for line in file_content.split('\n') if line.strip()]
            
            # Clean and validate usernames
            self.usernames = []
            invalid = []
            for username in raw_usernames:
                username = username.lstrip('@').strip()
                if re.match(r'^[a-zA-Z0-9_]{5,32}$', username):
                    self.usernames.append(username)
                else:
                    invalid.append(username)
            
            if not self.usernames:
                await update.message.reply_text(
                    "❌ No valid usernames found in the file. "
                    "Usernames must be 5-32 characters (letters, numbers, underscores)."
                )
                return FILE
                
            # Show summary
            message = (
                f"📊 File processed successfully:\n"
                f"• Valid usernames: {len(self.usernames)}\n"
                f"• Invalid entries: {len(invalid)}\n\n"
                f"Next, please send me the target group chat ID "
                f"(or forward a message from that group)."
            )
            
            if invalid:
                message += f"\n\nInvalid entries:\n" + "\n".join(invalid[:10])
                if len(invalid) > 10:
                    message += f"\n(and {len(invalid)-10} more...)"
            
            await update.message.reply_text(message)
            return CONFIRM
            
        except Exception as e:
            logger.error(f"File processing error: {e}")
            await update.message.reply_text("❌ Error processing file. Please try again.")
            return FILE

    async def confirm_and_add(self, update: Update, context: CallbackContext) -> int:
        """Handle group chat ID and start adding users."""
        try:
            # Try to get chat ID from forwarded message or direct input
            if update.message.forward_from_chat:
                chat_id = update.message.forward_from_chat.id
            else:
                chat_id = int(update.message.text.strip())
                
            # Verify bot is admin in target group
            try:
                bot = context.bot
                chat_member = await bot.get_chat_member(chat_id, bot.id)
                if chat_member.status != 'administrator' or not chat_member.can_invite_users:
                    await update.message.reply_text(
                        "❌ Bot must be admin in the target group with 'Add Users' permission."
                    )
                    return CONFIRM
            except TelegramError as e:
                await update.message.reply_text(f"❌ Error verifying bot admin status: {e}")
                return CONFIRM
            
            # Start adding users
            await update.message.reply_text(
                f"⏳ Starting to add {len(self.usernames)} users to group {chat_id}...\n"
                f"Estimated time: {len(self.usernames) * 20 / 60:.1f} minutes"
            )
            
            added = []
            failed = []
            skipped = []
            
            for username in self.usernames:
                if not await self.check_rate_limit():
                    skipped.append(username)
                    continue
                
                try:
                    # Check if user exists first
                    try:
                        user = await bot.get_chat(f"@{username}")
                        if user.type != 'private':
                            failed.append(f"{username} (not a user)")
                            continue
                    except BadRequest:
                        failed.append(f"{username} (not found)")
                        continue
                    
                    # Random delay
                    delay = random.uniform(self.MIN_DELAY_SECONDS, self.MAX_DELAY_SECONDS)
                    time.sleep(delay)
                    
                    # Add user
                    await bot.add_chat_member(
                        chat_id=chat_id,
                        user_id=user.id
                    )
                    
                    self.additions_count += 1
                    self.last_addition_time = time.time()
                    added.append(username)
                    
                    logger.info(f"Added @{username} to {chat_id}")
                    
                except TelegramError as e:
                    error_msg = str(e).lower()
                    if "user is already" in error_msg:
                        added.append(f"{username} (already member)")
                    elif "privacy" in error_msg:
                        failed.append(f"{username} (privacy restriction)")
                    elif "flood" in error_msg:
                        skipped.append(f"{username} (flood control)")
                        time.sleep(60)  # Longer wait if flood control triggered
                    else:
                        failed.append(f"{username} (error: {str(e)[:50]})")
                    
                    logger.warning(f"Failed to add @{username}: {e}")
            
            # Generate report
            report = (
                f"✅ Added: {len(added)}\n"
                f"❌ Failed: {len(failed)}\n"
                f"⚠️ Skipped: {len(skipped)}\n\n"
            )
            
            if added:
                report += "Added users:\n" + "\n".join([f"@{u}" for u in added[:10]])
                if len(added) > 10:
                    report += f"\n(and {len(added)-10} more...)"
            
            if failed:
                report += "\n\nFailed users:\n" + "\n".join(failed[:10])
                if len(failed) > 10:
                    report += f"\n(and {len(failed)-10} more...)"
            
            # Send report in chunks if too long
            for i in range(0, len(report), 4000):
                await update.message.reply_text(report[i:i+4000])
            
            # Reset for next operation
            self.usernames = []
            return ConversationHandler.END
            
        except ValueError:
            await update.message.reply_text("❌ Invalid chat ID. Please send a numeric chat ID or forward a group message.")
            return CONFIRM
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            await update.message.reply_text(f"❌ An error occurred: {str(e)[:200]}")
            return ConversationHandler.END

    async def check_rate_limit(self) -> bool:
        """Check if we can proceed with another addition."""
        current_time = time.time()
        
        # Reset counters if needed
        if current_time - self.reset_time > 3600:
            self.additions_count = 0
            self.reset_time = current_time
        
        # Check limits
        if self.additions_count >= min(self.MAX_ADDITIONS_PER_HOUR, self.MAX_DAILY_ADDITIONS):
            return False
        
        # Enforce delay
        time_since_last = current_time - self.last_addition_time
        if time_since_last < self.MIN_DELAY_SECONDS:
            time.sleep(self.MIN_DELAY_SECONDS - time_since_last)
        
        return True

    async def cancel(self, update: Update, context: CallbackContext) -> int:
        """Cancel the current operation."""
        await update.message.reply_text("Operation cancelled.")
        self.usernames = []
        return ConversationHandler.END

def main() -> None:
    """Run the bot."""
    application = Application.builder().token("7736244152:AAFk_42iceNa-cvvYw_eoAsKW7ckBHNgZfo").build()
    
    bot = GroupAdderBot()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', bot.start)],
        states={
            PHONE: [MessageHandler(Filters.text & ~Filters.command, bot.verify_phone)],
            FILE: [MessageHandler(Filters.document, bot.process_file)],
            CONFIRM: [MessageHandler(Filters.text | Filters.forwarded, bot.confirm_and_add)],
        },
        fallbacks=[CommandHandler('cancel', bot.cancel)],
    )
    
    application.add_handler(conv_handler)
    
    # Run the bot
    application.run_polling()

if __name__ == '__main__':
    main()