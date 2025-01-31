import requests
import time
import webbrowser
import subprocess
import argparse
import platform
import telegram
from telegram.ext import Application, CommandHandler
import asyncio
from datetime import datetime, timedelta
import signal
import sys

# Import winsound only on Windows
if platform.system() == 'Windows':
    import winsound

# Import configuration
from config import (
    PRODUCT_CONFIG,
    STATUS_UPDATES,
    TELEGRAM_CONFIG,
    NOTIFICATION_CONFIG,
    API_CONFIG
)

# Get enabled SKUs based on configuration
AVAILABLE_SKUS = {sku: config["name"] 
                 for sku, config in PRODUCT_CONFIG.items() 
                 if config["enabled"]}

# API configuration
API_URL = API_CONFIG["url"]
params = API_CONFIG["params"]
headers = API_CONFIG["headers"]
NVIDIA_BASE_URL = API_CONFIG["base_url"]

# Initialize global variables
def init_globals():
    global last_stock_status, start_time, successful_requests, failed_requests
    global last_check_time, last_check_success, last_console_status_time
    global last_telegram_status_time, telegram_bot, running, loop_manager
    
    last_stock_status = {}
    start_time = datetime.now()
    successful_requests = 0
    failed_requests = 0
    last_check_time = None
    last_check_success = True
    last_console_status_time = datetime.now()
    last_telegram_status_time = datetime.now()
    telegram_bot = None
    running = True
    loop_manager = None

# Initialize globals
init_globals()

class AsyncLoopManager:
    def __init__(self):
        self.loop = None
        
    def start_loop(self):
        """Start a new event loop in the current thread"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
    def stop_loop(self):
        """Stop the event loop"""
        if self.loop:
            self.loop.stop()
            self.loop.close()
            
    def run_coroutine(self, coro):
        """Run a coroutine in the current loop"""
        if self.loop:
            return self.loop.run_until_complete(coro)
        return None

class TelegramBot:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.bot = telegram.Bot(token=token)
        self.connected = False
        self.backoff_time = TELEGRAM_CONFIG["initial_backoff"]
        self.retry_count = 0
        self.last_connection_attempt = None
        self.application = None  # Add this line

    async def start(self):
        """Start the Telegram bot with error handling and backoff"""
        try:
            # Simple connection test
            await self.bot.get_me()
            self.connected = True
            self.backoff_time = TELEGRAM_CONFIG["initial_backoff"]
            self.retry_count = 0
            print(f"[{get_timestamp()}] ✅ Telegram bot connected successfully")

            # Initialize the application and add command handlers
            self.application = Application.builder().token(self.token).build()
            self.application.add_handler(CommandHandler("status", status_command))

            # Start polling
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()

        except Exception as e:
            self.connected = False
            print(f"[{get_timestamp()}] ❌ Telegram connection failed: {str(e)}")

    async def stop(self):
        """Stop the Telegram bot gracefully"""
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
        self.connected = False
        print(f"[{get_timestamp()}] ✅ Telegram bot stopped successfully")

    async def send_message(self, message):
        """Send a message with error handling"""
        if not self.connected or not running:
            return
            
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='HTML'
            )
        except Exception as e:
            print(f"[{get_timestamp()}] ❌ Failed to send Telegram message: {str(e)}")
            self.connected = False

# Add this function to handle the /status command
async def status_command(update, context):
    status_message = generate_status_message()
    await update.message.reply_text(status_message, parse_mode='HTML')

# Add this function to send a startup message
async def send_startup_message():
    """Send a startup message via Telegram with monitoring details"""
    if not TELEGRAM_CONFIG["enabled"] or not telegram_bot or not telegram_bot.connected:
        return

    # Generate the startup message
    startup_message = f"""🚀 NVIDIA Stock Checker Started Successfully!
🎯 Monitoring: {', '.join(AVAILABLE_SKUS.values())}
⏱️ Check Interval: {params['check_interval']} seconds
🔔 Notifications: {'Enabled' if NOTIFICATION_CONFIG['play_sound'] else 'Disabled'}
🌐 Browser Opening: {'Enabled' if NOTIFICATION_CONFIG['open_browser'] else 'Disabled'}"""

    try:
        await telegram_bot.send_message(startup_message)
        print(f"[{get_timestamp()}] ✅ Startup message sent to Telegram")
    except Exception as e:
        print(f"[{get_timestamp()}] ❌ Failed to send startup message: {str(e)}")

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global running
    print(f"\n[{get_timestamp()}] 🛑 Received shutdown signal, cleaning up...")
    running = False
    
    if telegram_bot:
        loop_manager.run_coroutine(telegram_bot.stop())
    if loop_manager:
        loop_manager.stop_loop()
    
    print(f"[{get_timestamp()}] Goodbye!")
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def get_timestamp():
    """Return current timestamp in a readable format"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def format_duration(duration):
    """Format a duration into a readable string"""
    hours = duration.seconds // 3600
    minutes = (duration.seconds % 3600) // 60
    return f"{hours} hours {minutes} minutes"

def generate_status_message():
    """Generate a status message for both console and Telegram"""
    runtime = datetime.now() - start_time
    
    status_check = "✅" if last_check_success else "❌"
    status_text = "Successful" if last_check_success else "Failed"
    
    if last_check_time:
        last_check_str = last_check_time.strftime("%H:%M:%S %d/%m/%Y")
    else:
        last_check_str = "No checks yet"

    return f"""📊 NVIDIA Stock Checker Status
⏱️ Running for: {format_duration(runtime)}
📈 Requests: {successful_requests:,} successful, {failed_requests:,} failed
{status_check} Last check: {last_check_str} ({status_text})
🎯 Monitoring: {', '.join(AVAILABLE_SKUS.values())}"""

def send_console_status():
    """Print a status update to the console"""
    global last_console_status_time
    if not running:
        return
    status_message = generate_status_message()
    print(f"\n[{get_timestamp()}] {status_message}\n")
    last_console_status_time = datetime.now()

def send_telegram_status():
    """Send a status update via Telegram"""
    global last_telegram_status_time
    if not running:
        return
    if not TELEGRAM_CONFIG["enabled"] or not telegram_bot or not telegram_bot.connected:
        return
    if not loop_manager:
        print(f"[{get_timestamp()}] ⚠️ Cannot send Telegram status: Loop manager not initialized")
        return

    try:
        status_message = generate_status_message()
        loop_manager.run_coroutine(telegram_bot.send_message(status_message))
    except Exception as e:
        print(f"[{get_timestamp()}] ❌ Failed to send Telegram status: {str(e)}")
    finally:
        last_telegram_status_time = datetime.now()

def play_notification_sound():
    """Play notification sound using the appropriate method for the OS"""
    if not NOTIFICATION_CONFIG["play_sound"] or not running:
        return

    system = platform.system()
    
    if system == 'Windows':
        try:
            winsound.MessageBeep()  # Built-in Windows alert sound
        except Exception as e:
            print(f"[{get_timestamp()}] ⚠️ Failed to play Windows sound: {e}")
    
    elif system == 'Darwin':  # macOS
        try:
            subprocess.run(['afplay', '/System/Library/Sounds/Ping.aiff'], check=True)
        except subprocess.SubprocessError as e:
            print(f"[{get_timestamp()}] ⚠️ Failed to play macOS sound: {e}")
    
    else:  # Linux or other systems
        print(f"[{get_timestamp()}] ℹ️ Sound not supported on this operating system")

def send_stock_notification(product_name, sku, price, product_url, in_stock):
    """Send stock notification via Telegram"""
    if not running:
        return
    if not TELEGRAM_CONFIG["enabled"] or not telegram_bot or not telegram_bot.connected:
        return
    if not loop_manager:
        print(f"[{get_timestamp()}] ⚠️ Cannot send Telegram notification: Loop manager not initialized")
        return

    status = "✅ IN STOCK" if in_stock else "❌ OUT OF STOCK"
    message = f"""🔔 NVIDIA Stock Alert
{status}: {product_name} ({sku})
💰 Price: £{price}
🔗 Link: {product_url}"""

    try:
        loop_manager.run_coroutine(telegram_bot.send_message(message))
    except Exception as e:
        print(f"[{get_timestamp()}] ❌ Failed to send Telegram notification: {str(e)}")

def notify_stock_available(product_url):
    """Open browser immediately and play notification sound after"""
    if not running:
        return
    if NOTIFICATION_CONFIG["open_browser"]:
        try:
            webbrowser.open(product_url)
        except Exception as e:
            print(f"[{get_timestamp()}] ⚠️ Failed to open browser: {e}")
    
    play_notification_sound()

def check_nvidia_stock(skus):
    """Check stock for each SKU individually"""
    global last_stock_status, successful_requests, failed_requests, last_check_time, last_check_success
    
    if not running:
        return
        
    current_time = datetime.now()
    
    # Check if it's time for console status update
    if STATUS_UPDATES["console"]["enabled"] and \
       (current_time - last_console_status_time).seconds >= STATUS_UPDATES["console"]["interval"]:
        send_console_status()
    
    # Check if it's time for Telegram status update
    if STATUS_UPDATES["telegram"]["enabled"] and \
       (current_time - last_telegram_status_time).seconds >= STATUS_UPDATES["telegram"]["interval"]:
        send_telegram_status()
    
    for sku in skus:
        if not running:
            return
            
        try:
            # Query one SKU at a time
            current_params = {**params, "skus": sku}
            
            response = requests.get(API_URL, params=current_params, headers=headers)
            response.raise_for_status()
            data = response.json()
            successful_requests += 1
            last_check_success = True
            last_check_time = datetime.now()

            if "listMap" in data and isinstance(data["listMap"], list):
                # Process response for this SKU
                if data["listMap"]:  # If we got data back
                    item = data["listMap"][0]  # Should only be one item
                    api_sku = item.get("fe_sku", "Unknown SKU")
                    is_active = item.get("is_active", "false") == "true"
                    price = item.get("price", "Unknown Price")
                    product_url = item.get("product_url") or NVIDIA_BASE_URL
                    
                    # Remove _UK suffix for matching
                    base_sku = api_sku.split('_')[0] if '_' in api_sku else api_sku
                    
                    if base_sku in AVAILABLE_SKUS:
                        product_name = AVAILABLE_SKUS[base_sku]
                        
                        # Check if stock status has changed
                        if api_sku not in last_stock_status or last_stock_status[api_sku] != is_active:
                            last_stock_status[api_sku] = is_active
                            timestamp = get_timestamp()

                            if is_active:
                                print(f"[{timestamp}] ✅ IN STOCK: {product_name} ({base_sku}) - £{price}")
                                print(f"[{timestamp}] 🔗 NVIDIA Link: {product_url}")
                                notify_stock_available(product_url)
                                # Send Telegram notification
                                send_stock_notification(product_name, base_sku, price, product_url, True)
                                time.sleep(params['cooldown'])
                            else:
                                print(f"[{timestamp}] ❌ OUT OF STOCK: {product_name} ({base_sku}) - £{price}")
                                send_stock_notification(product_name, base_sku, price, product_url, False)
                else:
                    # Empty listMap means product not in system
                    if sku in AVAILABLE_SKUS:
                        print(f"[{get_timestamp()}] ℹ️ {AVAILABLE_SKUS[sku]} ({sku}) is not currently in the system")
            
            # Small delay between requests to be nice to the API
            if running:
                time.sleep(1)

        except requests.exceptions.RequestException as e:
            failed_requests += 1
            last_check_success = False
            last_check_time = datetime.now()
            print(f"[{get_timestamp()}] ❌ API request failed for {sku}: {e}")

def run_test(skus):
    """Run a test of the notification system then transition to normal monitoring"""
    system = platform.system()
    print(f"[{get_timestamp()}] 🧪 Running test mode...")
    print(f"[{get_timestamp()}] Operating System: {system}")
    print(f"[{get_timestamp()}] Monitoring SKUs: {', '.join(skus)}")
    
    test_url = NVIDIA_BASE_URL
    print(f"[{get_timestamp()}] Testing stock notification...")
    notify_stock_available(test_url)  # This will open browser first, then play sound
    print(f"[{get_timestamp()}] ✅ Test completed. Browser should have opened and sound should have played.")
    
    if TELEGRAM_CONFIG["enabled"] and telegram_bot and telegram_bot.connected:
        print(f"[{get_timestamp()}] Testing Telegram notification...")
        send_stock_notification("Test Product", "TEST", "9.99", test_url, True)
        print(f"[{get_timestamp()}] Telegram test message sent.")
    
    # Simulate cooldown period
    print(f"[{get_timestamp()}] ⏳ Testing cooldown: waiting {params['cooldown']} seconds...")
    time.sleep(params['cooldown'])
    print(f"[{get_timestamp()}] Cooldown period complete.")
    
    # Start normal monitoring
    print(f"[{get_timestamp()}] Transitioning to normal monitoring mode...")
    while running:
        try:
            check_nvidia_stock(skus)
            if running:
                time.sleep(params['check_interval'])
        except Exception as e:
            print(f"[{get_timestamp()}] ❌ Error during monitoring: {str(e)}")
            if running:
                time.sleep(params['check_interval'])

def list_available_skus():
    """Print all available SKUs and their descriptions"""
    print("\nProduct Configuration:")
    for sku, config in PRODUCT_CONFIG.items():
        status = "✅ Enabled" if config["enabled"] else "❌ Disabled"
        print(f"  {sku}: {config['name']} - {status}")
    print("\nCurrently monitoring:")
    for sku, name in AVAILABLE_SKUS.items():
        print(f"  {sku}: {name}")
    print()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='NVIDIA Stock Checker')
    parser.add_argument('--skus', type=str, nargs='+',
                      help='SKUs to monitor (space-separated list, overrides configuration)')
    parser.add_argument('--test', action='store_true', 
                      help='Run in test mode to check notification system')
    parser.add_argument('--list-skus', action='store_true',
                      help='List all available SKUs and exit')
    parser.add_argument('--cooldown', type=int, default=params['cooldown'],
                      help=f'Cooldown period in seconds after finding stock (default: {params["cooldown"]})')
    parser.add_argument('--check-interval', type=int, default=params['check_interval'],
                      help=f'Time between checks in seconds (default: {params["check_interval"]})')
    parser.add_argument('--console-status', action='store_true',
                      help='Enable console status updates')
    parser.add_argument('--no-console-status', action='store_true',
                      help='Disable console status updates')
    parser.add_argument('--console-interval', type=int,
                      help='Time between console status updates in seconds')
    parser.add_argument('--telegram-status', action='store_true',
                      help='Enable Telegram status updates')
    parser.add_argument('--no-telegram-status', action='store_true',
                      help='Disable Telegram status updates')
    parser.add_argument('--telegram-interval', type=int,
                      help='Time between Telegram status updates in seconds')
    parser.add_argument('--telegram-token', type=str,
                      help='Telegram bot token')
    parser.add_argument('--telegram-chat-id', type=str,
                      help='Telegram chat ID')
    parser.add_argument('--telegram-polling-timeout', type=int,
                      help='Telegram polling timeout in seconds')
    parser.add_argument('--no-sound', action='store_true',
                      help='Disable notification sounds')
    parser.add_argument('--no-browser', action='store_true',
                      help='Disable automatic browser opening')
    
    args = parser.parse_args()
    
    if args.list_skus:
        list_available_skus()
        exit(0)
    
    # If SKUs specified via command line, use those instead of configuration
    selected_skus = args.skus if args.skus else list(AVAILABLE_SKUS.keys())
    
    # Validate SKUs
    invalid_skus = [sku for sku in selected_skus if sku not in PRODUCT_CONFIG]
    if invalid_skus:
        print(f"Error: Invalid SKU(s): {', '.join(invalid_skus)}")
        list_available_skus()
        exit(1)
    
    # Override params with command line arguments if provided
    params['cooldown'] = args.cooldown
    params['check_interval'] = args.check_interval
    
    # Process status update settings
    if args.console_status:
        STATUS_UPDATES["console"]["enabled"] = True
    if args.no_console_status:
        STATUS_UPDATES["console"]["enabled"] = False
    if args.console_interval:
        STATUS_UPDATES["console"]["interval"] = args.console_interval

    if args.telegram_status:
        STATUS_UPDATES["telegram"]["enabled"] = True
    if args.no_telegram_status:
        STATUS_UPDATES["telegram"]["enabled"] = False
    if args.telegram_interval:
        STATUS_UPDATES["telegram"]["interval"] = args.telegram_interval
    
    # Process notification configuration
    if args.no_sound:
        NOTIFICATION_CONFIG["play_sound"] = False
    if args.no_browser:
        NOTIFICATION_CONFIG["open_browser"] = False
    
    # Process Telegram arguments
    if args.telegram_token:
        TELEGRAM_CONFIG["bot_token"] = args.telegram_token
        TELEGRAM_CONFIG["enabled"] = True
    if args.telegram_chat_id:
        TELEGRAM_CONFIG["chat_id"] = args.telegram_chat_id
    if args.telegram_polling_timeout:
        TELEGRAM_CONFIG["polling_timeout"] = args.telegram_polling_timeout

    try:
        # Initialize loop manager if Telegram is enabled
        if TELEGRAM_CONFIG["enabled"]:
            if TELEGRAM_CONFIG["bot_token"] and TELEGRAM_CONFIG["chat_id"]:
                # Create and start the async loop manager
                loop_manager = AsyncLoopManager()
                loop_manager.start_loop()
                
                # Initialize and start the bot
                telegram_bot = TelegramBot(TELEGRAM_CONFIG["bot_token"], TELEGRAM_CONFIG["chat_id"])
                
                try:
                    loop_manager.run_coroutine(telegram_bot.start())
                    # Give the bot a moment to connect
                    time.sleep(2)
                    
                    if not telegram_bot.connected:
                        print(f"[{get_timestamp()}] ⚠️ Telegram bot failed to connect")
                        TELEGRAM_CONFIG["enabled"] = False
                        telegram_bot = None
                        loop_manager.stop_loop()
                        loop_manager = None
                    else:
                        # Send the startup message after successful connection
                        loop_manager.run_coroutine(send_startup_message())  # <-- Add this line
                except Exception as e:
                    print(f"[{get_timestamp()}] ❌ Failed to initialize Telegram bot: {str(e)}")
                    TELEGRAM_CONFIG["enabled"] = False
                    telegram_bot = None
                    loop_manager.stop_loop()
                    loop_manager = None
            else:
                print(f"[{get_timestamp()}] ℹ️ Telegram disabled: missing credentials")
                TELEGRAM_CONFIG["enabled"] = False

        if args.test:
            run_test(selected_skus)
        else:
            system = platform.system()
            print(f"[{get_timestamp()}] Stock checker started. Monitoring for changes...")
            print(f"[{get_timestamp()}] Operating System: {system}")
            print(f"[{get_timestamp()}] Monitoring SKUs: {', '.join(selected_skus)}")
            print(f"[{get_timestamp()}] Check Interval: {params['check_interval']} seconds")
            print(f"[{get_timestamp()}] Cooldown Period: {params['cooldown']} seconds")
            
            if STATUS_UPDATES["console"]["enabled"]:
                print(f"[{get_timestamp()}] Console Status Updates: Every {STATUS_UPDATES['console']['interval']} seconds")
            else:
                print(f"[{get_timestamp()}] Console Status Updates: Disabled")
                
            if STATUS_UPDATES["telegram"]["enabled"] and TELEGRAM_CONFIG["enabled"]:
                print(f"[{get_timestamp()}] Telegram Status Updates: Every {STATUS_UPDATES['telegram']['interval']} seconds")
            else:
                print(f"[{get_timestamp()}] Telegram Status Updates: Disabled")
                
            print(f"[{get_timestamp()}] Sound Notifications: {'Enabled' if NOTIFICATION_CONFIG['play_sound'] else 'Disabled'}")
            print(f"[{get_timestamp()}] Browser Opening: {'Enabled' if NOTIFICATION_CONFIG['open_browser'] else 'Disabled'}")
            print(f"[{get_timestamp()}] Tip: Run with --test to test notifications")
            print(f"[{get_timestamp()}] Tip: Run with --list-skus to see all available SKUs")
            
            while running:
                try:
                    check_nvidia_stock(selected_skus)
                    if running:
                        time.sleep(params['check_interval'])
                except Exception as e:
                    print(f"[{get_timestamp()}] ❌ Error during monitoring: {str(e)}")
                    if running:
                        time.sleep(params['check_interval'])
                        
    except Exception as e:
        print(f"[{get_timestamp()}] ❌ Fatal error: {str(e)}")
    finally:
        # Ensure clean shutdown
        running = False
        if telegram_bot and loop_manager:
            loop_manager.run_coroutine(telegram_bot.stop())