#!/usr/bin/env python3
"""
Competitor Price Tracker
Track competitor pricing changes and get alerts
"""

import os
import sys
import argparse
import json
import re
import time
from datetime import datetime
from typing import Dict, List, Optional
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import schedule

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

load_dotenv()

class CompetitorPriceTracker:
    def __init__(self):
        self.products_file = 'products.json'
        self.products = self.load_products()
        
        # Notification settings
        self.slack_webhook = os.getenv('SLACK_WEBHOOK_URL')
        self.email_to = os.getenv('EMAIL_TO')
    
    def load_products(self) -> Dict:
        """Load tracked products from file"""
        if os.path.exists(self.products_file):
            with open(self.products_file, 'r') as f:
                return json.load(f)
        return {'products': {}}
    
    def save_products(self):
        """Save tracked products to file"""
        with open(self.products_file, 'w') as f:
            json.dump(self.products, f, indent=2)
    
    def add_product(self, name: str, url: str, selector: str):
        """Add product to tracking"""
        if name in self.products['products']:
            print(f"⚠️  Product {name} already tracked")
            return
        
        # Get initial price
        price = self.get_price(url, selector)
        
        self.products['products'][name] = {
            'url': url,
            'selector': selector,
            'current_price': price,
            'previous_price': None,
            'added': datetime.now().isoformat(),
            'last_checked': datetime.now().isoformat(),
            'price_history': [{'date': datetime.now().isoformat(), 'price': price}] if price else []
        }
        
        self.save_products()
        print(f"✅ Added product: {name}")
        if price:
            print(f"   Current price: {price}")
    
    def remove_product(self, name: str):
        """Remove product from tracking"""
        if name in self.products['products']:
            del self.products['products'][name]
            self.save_products()
            print(f"✅ Removed product: {name}")
        else:
            print(f"❌ Product {name} not found")
    
    def get_price(self, url: str, selector: str) -> Optional[float]:
        """Extract price from URL using selector"""
        try:
            # Try with requests first
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            element = soup.select_one(selector)
            
            if element:
                price_text = element.get_text(strip=True)
                # Extract price (numbers, decimal point, currency symbols)
                price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                if price_match:
                    price = float(price_match.group().replace(',', ''))
                    return price
            
            # If selector didn't work, try Selenium for JS-rendered content
            if SELENIUM_AVAILABLE:
                return self.get_price_selenium(url, selector)
            
            return None
        except Exception as e:
            print(f"❌ Error fetching price from {url}: {e}")
            return None
    
    def get_price_selenium(self, url: str, selector: str) -> Optional[float]:
        """Get price using Selenium for JavaScript-rendered content"""
        if not SELENIUM_AVAILABLE:
            return None
        
        try:
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            
            driver = webdriver.Chrome(options=options)
            driver.get(url)
            time.sleep(3)  # Wait for JS to render
            
            from selenium.webdriver.common.by import By
            element = driver.find_element(By.CSS_SELECTOR, selector)
            price_text = element.text
            
            driver.quit()
            
            # Extract price
            price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
            if price_match:
                return float(price_match.group().replace(',', ''))
            
            return None
        except Exception as e:
            print(f"❌ Selenium error: {e}")
            return None
    
    def send_slack_alert(self, product_name: str, old_price: float, new_price: float, url: str):
        """Send Slack alert for price change"""
        if not self.slack_webhook:
            return
        
        try:
            change = new_price - old_price
            change_pct = (change / old_price) * 100
            direction = "📈 Increased" if change > 0 else "📉 Decreased"
            
            message = {
                "text": f"Price Change Alert: {product_name}",
                "attachments": [
                    {
                        "color": "#FF6B00" if change > 0 else "#36A64F",
                        "title": f"{direction}: {product_name}",
                        "fields": [
                            {
                                "title": "Old Price",
                                "value": f"${old_price:.2f}",
                                "short": True
                            },
                            {
                                "title": "New Price",
                                "value": f"${new_price:.2f}",
                                "short": True
                            },
                            {
                                "title": "Change",
                                "value": f"${abs(change):.2f} ({abs(change_pct):.1f}%)",
                                "short": True
                            }
                        ],
                        "actions": [
                            {
                                "type": "button",
                                "text": "View Product",
                                "url": url
                            }
                        ]
                    }
                ]
            }
            
            requests.post(self.slack_webhook, json=message)
        except Exception as e:
            print(f"❌ Slack alert error: {e}")
    
    def check_product(self, name: str) -> bool:
        """Check price for a single product"""
        product = self.products['products'].get(name)
        if not product:
            return False
        
        url = product['url']
        selector = product['selector']
        
        new_price = self.get_price(url, selector)
        
        if new_price is None:
            print(f"⚠️  Could not get price for {name}")
            return False
        
        old_price = product['current_price']
        product['last_checked'] = datetime.now().isoformat()
        
        # Check if price changed
        if old_price is not None and abs(new_price - old_price) > 0.01:  # Allow for small floating point differences
            print(f"🔔 Price change detected for {name}: ${old_price:.2f} → ${new_price:.2f}")
            
            product['previous_price'] = old_price
            product['current_price'] = new_price
            product['price_history'].append({
                'date': datetime.now().isoformat(),
                'price': new_price
            })
            
            # Send alert
            self.send_slack_alert(name, old_price, new_price, url)
            
            self.save_products()
            return True
        else:
            if old_price is None:
                product['current_price'] = new_price
                product['price_history'].append({
                    'date': datetime.now().isoformat(),
                    'price': new_price
                })
                self.save_products()
            
            print(f"✓ {name}: ${new_price:.2f} (no change)")
            return False
    
    def check_all_products(self):
        """Check all tracked products"""
        if not self.products['products']:
            print("No products to track. Add products with --add")
            return
        
        print(f"🔍 Checking {len(self.products['products'])} product(s)...")
        
        for name in list(self.products['products'].keys()):
            self.check_product(name)
            time.sleep(2)  # Rate limiting
    
    def list_products(self):
        """List all tracked products"""
        if not self.products['products']:
            print("No products tracked")
            return
        
        print("\n" + "="*80)
        print("TRACKED PRODUCTS")
        print("="*80)
        
        for name, product in self.products['products'].items():
            price = product.get('current_price')
            price_str = f"${price:.2f}" if price else "Unknown"
            
            print(f"\n{name}")
            print(f"  URL: {product['url']}")
            print(f"  Current Price: {price_str}")
            print(f"  Last Checked: {product.get('last_checked', 'Never')}")
    
    def show_history(self, name: str):
        """Show price history for a product"""
        product = self.products['products'].get(name)
        if not product:
            print(f"❌ Product {name} not found")
            return
        
        history = product.get('price_history', [])
        if not history:
            print(f"No price history for {name}")
            return
        
        print(f"\n📊 Price History for {name}")
        print("="*80)
        
        for entry in history[-10:]:  # Show last 10 entries
            date = datetime.fromisoformat(entry['date']).strftime('%Y-%m-%d %H:%M')
            print(f"{date}: ${entry['price']:.2f}")
    
    def run_continuous(self, interval: int = 3600):
        """Run continuous monitoring"""
        print(f"🚀 Starting continuous price tracking (checking every {interval}s)")
        
        schedule.every(interval).seconds.do(self.check_all_products)
        
        # Initial check
        self.check_all_products()
        
        while True:
            schedule.run_pending()
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description='Competitor Price Tracker')
    parser.add_argument('--add', help='Product name to add')
    parser.add_argument('--url', help='Product URL')
    parser.add_argument('--selector', help='CSS selector for price element')
    parser.add_argument('--remove', help='Remove product from tracking')
    parser.add_argument('--list', action='store_true', help='List tracked products')
    parser.add_argument('--check', action='store_true', help='Check all products')
    parser.add_argument('--history', help='Show price history for product')
    parser.add_argument('--watch', action='store_true', help='Run continuous monitoring')
    parser.add_argument('--interval', type=int, default=3600, help='Check interval in seconds')
    
    args = parser.parse_args()
    
    try:
        tracker = CompetitorPriceTracker()
        
        if args.add and args.url and args.selector:
            tracker.add_product(args.add, args.url, args.selector)
        elif args.remove:
            tracker.remove_product(args.remove)
        elif args.list:
            tracker.list_products()
        elif args.history:
            tracker.show_history(args.history)
        elif args.check:
            tracker.check_all_products()
        elif args.watch:
            tracker.run_continuous(args.interval)
        else:
            parser.print_help()
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

