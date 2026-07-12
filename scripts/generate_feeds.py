import os
import yaml
import markdown
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

def generate_rss(posts, config, output_path):
    site_cfg = config['site']
    blog_cfg = config['blog']
    
    rss = ET.Element('rss', version='2.0')
    channel = ET.SubElement(rss, 'channel')
    
    ET.SubElement(channel, 'title').text = site_cfg['title']
    ET.SubElement(channel, 'link').text = f"{site_cfg['url']}{blog_cfg['base_path']}"
    ET.SubElement(channel, 'description').text = site_cfg['description']
    ET.SubElement(channel, 'language').text = site_cfg['language']
    
    for post in posts:
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = post.get('title')
        post_url = f"{site_cfg['url']}{blog_cfg['base_path']}/{post.get('slug')}"
        ET.SubElement(item, 'link').text = post_url
        
        # RSS description often contains HTML content
        ET.SubElement(item, 'description').text = post.get('excerpt', post.get('body_html')[:500] + '...')
        
        # Format date for RSS: Mon, 02 Jan 2006 15:04:05 +0000
        try:
            dt = datetime.strptime(post.get('date'), '%Y-%m-%d')
            ET.SubElement(item, 'pubDate').text = dt.strftime('%a, %d %b %Y %H:%M:%S +0000')
        except ValueError:
            pass # Skip if date format is invalid

        ET.SubElement(item, 'guid').text = post_url
        ET.SubElement(item, 'author').text = post.get('author', site_cfg['author'])

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(prettify(rss))

def generate_atom(posts, config, output_path):
    site_cfg = config['site']
    blog_cfg = config['blog']
    
    atom = ET.Element('feed', xmlns='http://www.w3.org/2005/Atom')
    
    ET.SubElement(atom, 'title').text = site_cfg['title']
    if 'subtitle' in site_cfg:
        ET.SubElement(atom, 'subtitle').text = site_cfg['subtitle']
    
    atom_url = f"{site_cfg['url']}/atom.xml"
    ET.SubElement(atom, 'link', href=atom_url, rel='self')
    ET.SubElement(atom, 'link', href=f"{site_cfg['url']}{blog_cfg['base_path']}")
    ET.SubElement(atom, 'id').text = f"{site_cfg['url']}{blog_cfg['base_path']}"
    ET.SubElement(atom, 'updated').text = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
    
    author = ET.SubElement(atom, 'author')
    ET.SubElement(author, 'name').text = site_cfg['author']

    for post in posts:
        entry = ET.SubElement(atom, 'entry')
        ET.SubElement(entry, 'title').text = post.get('title')
        post_url = f"{site_cfg['url']}{blog_cfg['base_path']}/{post.get('slug')}"
        ET.SubElement(entry, 'link', href=post_url)
        ET.SubElement(entry, 'id').text = post_url
        
        try:
            dt = datetime.strptime(post.get('date'), '%Y-%m-%d')
            ET.SubElement(entry, 'updated').text = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        except ValueError:
            pass

        ET.SubElement(entry, 'summary').text = post.get('excerpt', post.get('body_html')[:200] + '...')
        
        content = ET.SubElement(entry, 'content', type='html')
        content.text = post.get('body_html')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(prettify(atom))

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
    generate_rss(posts, config, os.path.join(public_dir, 'rss.xml'))
    generate_atom(posts, config, os.path.join(public_dir, 'atom.xml'))
    print(f"Generated feeds for {len(posts)} posts in '{public_dir}/' directory.")

if __name__ == '__main__':
    main()
