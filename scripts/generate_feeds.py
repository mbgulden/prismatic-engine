import os
import yaml
import markdown
import json
from datetime import datetime
import xml.etree.ElementTree as ET
from xml.dom import minidom

def parse_markdown(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Split frontmatter and body
    parts = content.split('---', 2)
    if len(parts) < 3:
        return None
    
    try:
        metadata = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
        
    body_md = parts[2].strip()
    # Convert markdown to HTML
    metadata['body_html'] = markdown.markdown(body_md)
    metadata['slug'] = os.path.splitext(os.path.basename(filepath))[0]
    
    # Ensure date is a string in YYYY-MM-DD format
    if isinstance(metadata.get('date'), (datetime,)):
        metadata['date'] = metadata['date'].strftime('%Y-%m-%d')
    elif not isinstance(metadata.get('date'), str):
        metadata['date'] = str(metadata.get('date'))
        
    return metadata

def prettify(elem):
    """Return a pretty-printed XML string for the Element."""
    rough_string = ET.tostring(elem, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")

def generate_atom(posts, config, output_path):
    site_cfg = config['site']
    blog_cfg = config['blog']
    
    NS = 'http://www.w3.org/2005/Atom'
    ET.register_namespace('', NS)
    
    atom = ET.Element(f'{{{NS}}}feed')
    
    ET.SubElement(atom, f'{{{NS}}}title').text = site_cfg['title']
    if 'subtitle' in site_cfg:
        ET.SubElement(atom, f'{{{NS}}}subtitle').text = site_cfg['subtitle']
    
    atom_url = f"{site_cfg['url']}/feed.xml"
    ET.SubElement(atom, f'{{{NS}}}link', href=atom_url, rel='self')
    ET.SubElement(atom, f'{{{NS}}}link', href=f"{site_cfg['url']}{blog_cfg['base_path']}")
    ET.SubElement(atom, f'{{{NS}}}id').text = f"{site_cfg['url']}{blog_cfg['base_path']}"
    ET.SubElement(atom, f'{{{NS}}}updated').text = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
    
    author = ET.SubElement(atom, f'{{{NS}}}author')
    ET.SubElement(author, f'{{{NS}}}name').text = site_cfg['author']

    for post in posts:
        entry = ET.SubElement(atom, f'{{{NS}}}entry')
        ET.SubElement(entry, f'{{{NS}}}title').text = post.get('title')
        post_url = f"{site_cfg['url']}{blog_cfg['base_path']}/{post.get('slug')}"
        ET.SubElement(entry, f'{{{NS}}}link', href=post_url)
        ET.SubElement(entry, f'{{{NS}}}id').text = post_url
        
        try:
            dt = datetime.strptime(post.get('date'), '%Y-%m-%d')
            formatted_date = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            ET.SubElement(entry, f'{{{NS}}}updated').text = formatted_date
            ET.SubElement(entry, f'{{{NS}}}published').text = formatted_date
        except ValueError:
            pass

        ET.SubElement(entry, f'{{{NS}}}summary').text = post.get('excerpt', post.get('body_html')[:200] + '...')
        
        # Author details
        entry_author = ET.SubElement(entry, f'{{{NS}}}author')
        ET.SubElement(entry_author, f'{{{NS}}}name').text = post.get('author', site_cfg['author'])
        
        content = ET.SubElement(entry, f'{{{NS}}}content', type='html')
        content.text = post.get('body_html')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(prettify(atom))

def generate_json_feed(posts, config, output_path):
    site_cfg = config['site']
    blog_cfg = config['blog']
    
    feed = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": site_cfg['title'],
        "home_page_url": f"{site_cfg['url']}{blog_cfg['base_path']}",
        "feed_url": f"{site_cfg['url']}/feed.json",
        "description": site_cfg['description'],
        "items": []
    }
    
    for post in posts:
        post_url = f"{site_cfg['url']}{blog_cfg['base_path']}/{post.get('slug')}"
        item = {
            "id": post_url,
            "url": post_url,
            "title": post.get('title'),
            "summary": post.get('excerpt', post.get('body_html')[:200] + '...'),
            "content_html": post.get('body_html'),
        }
        
        try:
            dt = datetime.strptime(post.get('date'), '%Y-%m-%d')
            item["date_published"] = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        except ValueError:
            pass
            
        author_name = post.get('author', site_cfg['author'])
        item["authors"] = [{"name": author_name}]
        
        feed["items"].append(item)
        
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(feed, f, indent=2, ensure_ascii=False)

def main():
    config_path = os.environ.get("MARKETING_CONFIG", "config/marketing.yaml")
    if not os.path.exists(config_path):
        print(f"Error: Config not found at {config_path}")
        return

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    blog_dir = config['blog']['content_dir']
    public_dir = config['blog']['public_dir']
    
    posts = []
    if os.path.exists(blog_dir):
        for filename in os.listdir(blog_dir):
            if filename.endswith('.md'):
                post = parse_markdown(os.path.join(blog_dir, filename))
                if post:
                    posts.append(post)
    
    # Sort posts by date descending
    posts.sort(key=lambda x: x.get('date', ''), reverse=True)
    
    os.makedirs(public_dir, exist_ok=True)
    generate_atom(posts, config, os.path.join(public_dir, 'feed.xml'))
    generate_json_feed(posts, config, os.path.join(public_dir, 'feed.json'))
    print(f"Generated feeds in '{public_dir}/' directory.")

if __name__ == '__main__':
    main()
