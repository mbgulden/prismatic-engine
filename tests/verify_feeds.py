import os
import xml.etree.ElementTree as ET
import yaml

def test_rss_feed(config):
    path = os.path.join(config['blog']['public_dir'], 'rss.xml')
    assert os.path.exists(path), "RSS feed file missing"
    tree = ET.parse(path)
    root = tree.getroot()
    assert root.tag == 'rss', "Root tag is not 'rss'"
    assert root.attrib['version'] == '2.0', "RSS version is not 2.0"
    channel = root.find('channel')
    assert channel is not None, "Channel element missing"
    assert channel.find('title').text == config['site']['title'], "Incorrect channel title"
    items = channel.findall('item')
    assert len(items) > 0, "No items found in RSS feed"
    print(f"RSS feed validation passed ({len(items)} items).")

def test_atom_feed(config):
    path = os.path.join(config['blog']['public_dir'], 'atom.xml')
    assert os.path.exists(path), "Atom feed file missing"
    # Atom uses namespaces
    namespaces = {'atom': 'http://www.w3.org/2005/Atom'}
    tree = ET.parse(path)
    root = tree.getroot()
    assert root.tag == '{http://www.w3.org/2005/Atom}feed', f"Root tag is {root.tag}, expected Atom feed tag"
    title = root.find('atom:title', namespaces)
    assert title.text == config['site']['title'], "Incorrect feed title"
    entries = root.findall('atom:entry', namespaces)
    assert len(entries) > 0, "No entries found in Atom feed"
    
    # Check for HTML content in first entry
    first_entry = entries[0]
    content = first_entry.find('atom:content', namespaces)
    assert content.attrib['type'] == 'html', "Content type is not html"
    # The text is escaped in the XML file, so we check for escaped tags
    assert '&lt;h1&gt;' in content.text or '<h1>' in content.text, "Content does not appear to be HTML"
    
    print(f"Atom feed validation passed ({len(entries)} entries).")

if __name__ == '__main__':
    config_path = os.environ.get("MARKETING_CONFIG", "config/marketing.yaml")
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
        
    try:
        test_rss_feed(config)
        test_atom_feed(config)
        print("All feed verifications passed.")
    except AssertionError as e:
        print(f"Verification FAILED: {e}")
        exit(1)
