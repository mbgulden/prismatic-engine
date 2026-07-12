import os
import sys
import yaml
import json
import xml.etree.ElementTree as ET
import pytest
from pathlib import Path

# Ensure repo root is in python path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.generate_feeds import main as generate_main
from plugins.pwp.compiler import render_template

def test_feed_generation_and_metadata(tmp_path, monkeypatch):
    # Setup test config
    config = {
        'site': {
            'title': 'Test Blog Title',
            'subtitle': 'A test blog subtitle',
            'url': 'https://testsite.com',
            'description': 'Test Description for the blog.',
            'language': 'en-us',
            'author': 'Test Author'
        },
        'blog': {
            'content_dir': str(tmp_path / 'content/blog'),
            'public_dir': str(tmp_path / 'public'),
            'base_path': '/blog'
        }
    }
    
    # Create test blog posts
    blog_dir = tmp_path / 'content/blog'
    blog_dir.mkdir(parents=True)
    
    post1_content = """---
title: First Post
date: 2026-07-01
author: Alice
excerpt: Summary of first post.
---
# Hello World
Body of first post.
"""
    post2_content = """---
title: Second Post
date: 2026-07-02
author: Bob
excerpt: Summary of second post.
---
# Hello Again
Body of second post.
"""
    (blog_dir / "post1.md").write_text(post1_content, encoding='utf-8')
    (blog_dir / "post2.md").write_text(post2_content, encoding='utf-8')
    
    # Write temp config file
    config_file = tmp_path / 'marketing.yaml'
    with open(config_file, 'w', encoding='utf-8') as f:
        yaml.dump(config, f)
        
    # Mock environment variable for config path
    monkeypatch.setenv("MARKETING_CONFIG", str(config_file))
    
    # Run the generate feeds script
    generate_main()
    
    # Verify feed.xml exists and is correct
    feed_xml_path = tmp_path / 'public/feed.xml'
    assert feed_xml_path.exists(), "feed.xml was not created"
    
    # Parse feed.xml as Atom 1.0
    namespaces = {'atom': 'http://www.w3.org/2005/Atom'}
    tree = ET.parse(feed_xml_path)
    root = tree.getroot()
    assert root.tag == '{http://www.w3.org/2005/Atom}feed', "Root tag is not Atom feed"
    
    title = root.find('atom:title', namespaces)
    assert title.text == 'Test Blog Title'
    
    entries = root.findall('atom:entry', namespaces)
    assert len(entries) == 2, "Should have 2 entries"
    
    # Atom entries are sorted descending by date, so post2 should be first
    assert entries[0].find('atom:title', namespaces).text == 'Second Post'
    assert entries[1].find('atom:title', namespaces).text == 'First Post'
    
    # Verify elements are correct
    entry1 = entries[0]
    assert entry1.find('atom:link', namespaces).attrib['href'] == 'https://testsite.com/blog/post2'
    assert entry1.find('atom:summary', namespaces).text == 'Summary of second post.'
    assert entry1.find('atom:author', namespaces).find('atom:name', namespaces).text == 'Bob'
    assert entry1.find('atom:published', namespaces).text == '2026-07-02T00:00:00Z'
    
    # Verify feed.json exists and is correct
    feed_json_path = tmp_path / 'public/feed.json'
    assert feed_json_path.exists(), "feed.json was not created"
    
    with open(feed_json_path, 'r', encoding='utf-8') as f:
        feed_data = json.load(f)
        
    assert feed_data['version'] == 'https://jsonfeed.org/version/1.1'
    assert feed_data['title'] == 'Test Blog Title'
    assert feed_data['home_page_url'] == 'https://testsite.com/blog'
    assert len(feed_data['items']) == 2
    
    item1 = feed_data['items'][0]
    assert item1['title'] == 'Second Post'
    assert item1['url'] == 'https://testsite.com/blog/post2'
    assert item1['summary'] == 'Summary of second post.'
    assert item1['authors'][0]['name'] == 'Bob'
    assert item1['date_published'] == '2026-07-02T00:00:00Z'

def test_templates_contain_auto_discovery():
    # Verify templates render with the auto-discovery link injected or pre-existing in <head>
    for template in ['saas', 'corporate', 'portfolio']:
        html_content = render_template(template)
        assert 'rel="alternate"' in html_content
        assert 'type="application/atom+xml"' in html_content
        assert 'href="/feed.xml"' in html_content
