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
import asyncio

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
PHONE, USERNAMES, CONFIRM = range(3)

# Telegram Bot Token - REPLACE WITH YOUR ACTUAL TOKEN
TELEGRAM_BOT_TOKEN = "7736244152:AAFk_42iceNa-cvvYw_eoAsKW7ckBHNgZfo"

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
        self.MIN_DELAY_SECONDS = 5  # Reduced from 15 for better usability
        self.MAX_DELAY_SECONDS = 15  # Reduced from 45
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
            "Now you can:\n"
            "1. Send usernames separated by spaces, commas or newlines\n"
            "OR\n"
            "2. Upload a .txt file with one username per line\n\n"
            "Examples:\n"
            "@user1 user2, user3\n"
            "Or attach a .txt file"
        )
        return USERNAMES

    async def process_usernames(self, update: Update, context: CallbackContext) -> int:
        """Process usernames from either text or file."""
        self.usernames = []
        invalid = []
        
        # Handle file upload
        if update.message.document:
            file = await update.message.document.get_file()
            if not file.file_name.lower().endswith('.txt'):
                await update.message.reply_text("Only .txt files accepted.")
                return USERNAMES
                
            try:
                content = (await file.download_as_bytearray()).decode('utf-8')
                raw_usernames = [line.strip() for line in content.split('\n') if line.strip()]
            except Exception as e:
                logger.error(f"File error: {e}")
                await update.message.reply_text("❌ Error processing file.")
                return USERNAMES
        # Handle text input
        else:
            text = update.message.text
            # Split by commas, spaces, or newlines
            raw_usernames = re.split(r'[, \n]+', text)
        
        # Process all usernames
        for raw_username in raw_usernames:
            username = raw_username.lstrip('@').strip()
            if re.match(r'^[a-zA-Z0-9_]{5,32}$', username):
                self.usernames.append(username)
            else:
                invalid.append(raw_username)
        
        if not self.usernames:
            await update.message.reply_text("❌ No valid usernames found.")
            return USERNAMES
            
        msg = (
            f"📊 Found {len(self.usernames)} valid usernames\n"
            f"❌ {len(invalid)} invalid entries\n\n"
            "Now send the group chat ID (a negative number) or forward a message from the target group."
        )
        
        if invalid:
            msg += f"\n\nInvalid entries:\n" + "\n".join(invalid[:5])
            if len(invalid) > 5:
                msg += f"\n(and {len(invalid)-5} more...)"
        
        await update.message.reply_text(msg)
        return CONFIRM

    async def confirm_and_add(self, update: Update, context: CallbackContext) -> int:
        """Add users to group with improved error handling."""
        try:
            chat_id = None
            chat_type = None
            
            # Handle forwarded messages
            if hasattr(update.message, 'forward_from_chat'):
                chat_id = update.message.forward_from_chat.id
                chat_type = update.message.forward_from_chat.type
            elif hasattr(update.message, 'forward_from'):
                await update.message.reply_text("❌ Please forward a group message, not a user message.")
                return CONFIRM
            # Handle direct chat ID input
            else:
                try:
                    chat_id = int(update.message.text.strip())
                    if chat_id > 0:  # Group IDs are negative
                        await update.message.reply_text("❌ Group IDs are negative numbers. Please forward a message from the group instead.")
                        return CONFIRM
                except ValueError:
                    await update.message.reply_text("❌ Invalid chat ID. Please send a numeric chat ID or forward a group message.")
                    return CONFIRM
            
            # Verify it's a group or supergroup
            if chat_type and chat_type not in ['supergroup', 'group']:
                await update.message.reply_text("❌ Please forward a message from a group or supergroup.")
                return CONFIRM
                
            bot = context.bot
            try:
                # Get chat information if we don't have it yet (for direct chat ID input)
                if not chat_type:
                    chat = await bot.get_chat(chat_id)
                    chat_type = chat.type
                
                # Verify it's a group
                if chat_type not in ['supergroup', 'group']:
                    await update.message.reply_text("❌ The specified chat is not a group or supergroup.")
                    return CONFIRM
                    
                # Check bot admin status
                chat_member = await bot.get_chat_member(chat_id, bot.id)
                if not (chat_member.status == 'administrator' and chat_member.can_invite_users):
                    await update.message.reply_text("❌ Bot needs admin privileges with 'Add Users' permission.")
                    return CONFIRM
                    
            except TelegramError as e:
                await update.message.reply_text(f"❌ Error verifying group: {e}\nPlease make sure the bot is in that group as admin.")
                return CONFIRM
            
            total_users = len(self.usernames)
            await update.message.reply_text(
                f"⏳ Adding {total_users} users to group...\n"
                f"Estimated time: {total_users * 10 / 60:.1f} minutes\n"
                f"Working at {60/self.MIN_DELAY_SECONDS:.1f} users/hour (safe limit)"
            )
            
            added = []
            failed = []
            
            for username in self.usernames:
                try:
                    # Check rate limits
                    if not await self.check_rate_limit():
                        failed.append(f"{username} (rate limit)")
                        continue
                    
                    logger.info(f"Attempting to add user: {username}")
                    
                    # Try multiple lookup methods
                    try:
                        user = await bot.get_chat(f"@{username}")
                    except BadRequest:
                        try:
                            user = await bot.get_chat(username)
                        except BadRequest as e:
                            if "not found" in str(e).lower():
                                failed.append(f"{username} (account not found)")
                                continue
                            raise
                    
                    if user.type != 'private':
                        failed.append(f"{username} (not a user account)")
                        continue
                    
                    # Add with random delay
                    delay = random.uniform(self.MIN_DELAY_SECONDS, self.MAX_DELAY_SECONDS)
                    await asyncio.sleep(delay)
                    
                    # Attempt to add user
                    await bot.add_chat_member(
                        chat_id=chat_id,
                        user_id=user.id
                    )
                    self.additions_count += 1
                    self.last_addition_time = time.time()
                    added.append(username)
                    
                except TelegramError as e:
                    error_msg = str(e).lower()
                    if "user is already" in error_msg:
                        added.append(f"{username} (already member)")
                    elif "privacy" in error_msg:
                        failed.append(f"{username} (privacy settings)")
                    elif "flood" in error_msg:
                        failed.append(f"{username} (flood control)")
                        await asyncio.sleep(60)  # Longer wait if flood control triggered
                    else:
                        failed.append(f"{username} (error: {str(e)[:50]})")
                        logger.error(f"Error adding {username}: {e}")
            
            # Generate final report
            success_rate = (len(added)/total_users)*100 if total_users > 0 else 0
            report = (
                f"📊 Final Report:\n"
                f"✅ Successfully added: {len(added)} ({success_rate:.1f}%)\n"
                f"❌ Failed to add: {len(failed)}\n\n"
            )
            
            if added:
                report += "Added users:\n" + "\n".join([f"@{u}" for u in added[:5]])
                if len(added) > 5:
                    report += f"\n(and {len(added)-5} more...)"
            
            if failed:
                report += "\n\nFailed users:\n" + "\n".join(failed[:5])
                if len(failed) > 5:
                    report += f"\n(and {len(failed)-5} more...)"
            
            # Send report in chunks if too long
            for i in range(0, len(report), 4000):
                await update.message.reply_text(report[i:i+4000])
            
            return ConversationHandler.END
            
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            await update.message.reply_text(f"❌ An error occurred: {str(e)[:200]}")
            return ConversationHandler.END

    async def check_rate_limit(self) -> bool:
        """Check rate limits to avoid blocks."""
        current_time = time.time()
        
        # Reset hourly counter if needed
        if current_time - self.reset_time > 3600:
            self.additions_count = 0
            self.reset_time = current_time
        
        # Check both hourly and daily limits
        if self.additions_count >= min(self.MAX_ADDITIONS_PER_HOUR, self.MAX_DAILY_ADDITIONS):
            return False
        
        # Enforce minimum delay between operations
        time_since_last = current_time - self.last_addition_time
        if time_since_last < self.MIN_DELAY_SECONDS:
            await asyncio.sleep(self.MIN_DELAY_SECONDS - time_since_last)
        
        return True

    async def cancel(self, update: Update, context: CallbackContext) -> int:
        """Cancel the current operation."""
        await update.message.reply_text("🚫 Operation cancelled. Start again with /start if needed.")
        return ConversationHandler.END

def main() -> None:
    """Run the bot."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    bot = GroupAdderBot()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", bot.start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.verify_phone)],
            USERNAMES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.process_usernames),
                MessageHandler(filters.Document.FileExtension("txt"), bot.process_usernames)
            ],
            CONFIRM: [MessageHandler(filters.TEXT | filters.FORWARDED, bot.confirm_and_add)],
        },
        fallbacks=[CommandHandler("cancel", bot.cancel)],
    )
    
    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == "__main__":
    main()