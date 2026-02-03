import glob
import re
import codecs
import html

# List of files to process
files = [
    ('cat_starter_cold.html', 'STARTER - Cold Mezze'),
    ('cat_starter_hot.html', 'STARTER - Hot Mezze'),
    ('cat_starter_salad.html', 'STARTER - Salad'),
    ('cat_iyi.html', 'IYI SPECIAL'),
    ('cat_baked.html', 'BAKED MEAT'),
    ('cat_bbq_chicken.html', 'BBQ - Chicken'),
    ('cat_bbq_lamb.html', 'BBQ - Lamb/Beef'),
    ('cat_bbq_seafood.html', 'BBQ - Seafood'),
    ('cat_desserts.html', 'DESSERTS'),
    ('cat_drinks.html', 'DRINKS')
]

full_menu_text = ""

for filename, category_name in files:
    try:
        with open(filename, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        full_menu_text += f"\n\n--- {category_name} ---\n\n"
        
        # Regex to find product titles
        # Pattern looks for <h2 class="woocommerce-loop-product__title">Title</h2>
        # and <span class="price">...</span>
        
        # We can try to split by "product type-product" to isolate items
        products = re.split(r'class="[^"]*product type-product[^"]*"', content)
        
        if len(products) < 2:
             full_menu_text += "(No products found in this category parsing)\n"
        
        # Skip the first split as it is before the first product
        for prod_html in products[1:]:
            # Clean up the html chunk
            
            # Title
            title_match = re.search(r'<h2 class="woocommerce-loop-product__title">([^<]+)</h2>', prod_html)
            title = title_match.group(1).strip() if title_match else "Unknown Dish"
            title = html.unescape(title)
            
            # Price
            # content is like: <span class="price"><span class="amount"><bdi><span class="symbol">£</span>7.00</bdi></span></span>
            # We match until </bdi></span></span> to handle nested tags
            price_match = re.search(r'<span class="price">(.+?)</bdi></span></span>', prod_html, re.DOTALL)
            if price_match:
                price_html = price_match.group(1)
                # Remove html tags
                price = re.sub(r'<[^>]+>', '', price_html).strip()
                price = re.sub(r'\s+', ' ', price).strip()
                price = html.unescape(price)
            else:
                # Fallback for simple price or ranges (maybe no bdi?)
                price_match_simple = re.search(r'<span class="price">(.+?)</span>', prod_html, re.DOTALL)
                if price_match_simple:
                     # This might fail on nested, but worth a try if the other failed
                     # But actually, if nested, this returns truncated.
                     # Better to not fallback to broken one.
                     pass 
                price = "Price Varies"

            if price == "Price Varies":
                 # Try finding just the text after currency symbol if possible?
                 # Or maybe the structure is different (e.g. <span class="price"><ins>...</ins></span> for sale)
                 # Let's try matching <span class="price"> then grep for digits.
                 if 'class="price"' in prod_html:
                     # very rough extraction: find text after class="price"
                     p_start = prod_html.find('class="price"')
                     p_end = prod_html.find('</span></span>', p_start)
                     if p_start != -1 and p_end != -1:
                         raw_p = prod_html[p_start:p_end+14] # +14 is len(</span></span>)
                         clean_p = re.sub(r'<[^>]+>', '', raw_p).strip()
                         # if it contains digits
                         if any(c.isdigit() for c in clean_p):
                             price = html.unescape(clean_p)
                             # Cleanup "Original price was..." if it exists (WooCommerce sale)
                             # But let's assume it's just price for now. 
            
            # Description
            desc_match = re.search(r'<div class="product-short-description">(.+?)</div>', prod_html, re.DOTALL)
            description = ""
            if desc_match:
                desc_html = desc_match.group(1)
                description = re.sub(r'<[^>]+>', '', desc_html).strip()
                description = re.sub(r'\s+', ' ', description).strip()
                description = html.unescape(description)

            if title != "Unknown Dish":
                if description:
                    full_menu_text += f"{title} ({price}): {description}\n"
                else:
                    full_menu_text += f"{title} ({price})\n"
            
    except Exception as e:
        full_menu_text += f"\nError processing {filename}: {str(e)}\n"

print(full_menu_text)
import os
os.makedirs('data', exist_ok=True)
with open('data/carnivore_menu.txt', 'w', encoding='utf-8') as f:
    f.write(full_menu_text)
