from sqlalchemy import create_engine, Column, Integer, String, Float, Table, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
import threading
import time
from queue import PriorityQueue
import requests
from bs4 import BeautifulSoup

TRUST_THRESHOLD = 0.001
crawl_queue = PriorityQueue()
RUN_CRAWLER = True
DATABASE_URL = "sqlite:///./crawl_db.sqlite"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Association table for links
links_table = Table(
    'links',
    Base.metadata,
    Column('from_page_id', Integer, ForeignKey('pages.id'), primary_key=True),
    Column('to_page_id', Integer, ForeignKey('pages.id'), primary_key=True)
)

# Association table for page-keyword relationships
page_keywords_table = Table(
    'page_keywords',
    Base.metadata,
    Column('page_id', Integer, ForeignKey('pages.id'), primary_key=True),
    Column('keyword_id', Integer, ForeignKey('keywords.id'), primary_key=True)
)

class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, primary_key=True, index=True)
    word = Column(String, unique=True, index=True)

class Page(Base):
    __tablename__ = "pages"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True)
    title = Column(String)
    trust = Column(Float, default=1.0)
    linked_pages = relationship(
        "Page",
        secondary=links_table,
        primaryjoin=(id == links_table.c.from_page_id),
        secondaryjoin=(id == links_table.c.to_page_id),
        backref="incoming_links"
    )
    keywords = relationship(
        "Keyword",
        secondary=page_keywords_table,
        backref="pages"
    )


Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def fetch_page_data(url: str):
    """
    Fetches the page and extracts title, keywords (from meta tags), and links.
    """
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        title = soup.title.string if soup.title else url

        text_content = soup.get_text()
        lowercase_text = text_content.lower()
        keywords = lowercase_text.split()

        found_links = []
        for link_tag in soup.find_all('a', href=True):
            link_url = link_tag['href']
            if link_url.startswith('//'):
                link_url = "http:" + link_url
            elif link_url.startswith('/'):
                from urllib.parse import urljoin
                link_url = urljoin(url, link_url)

            if link_url.startswith('http'):
                found_links.append(link_url)

        return title, keywords, found_links

    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None, None, []


def add_keywords_to_page(db, page, keyword_list):
    """
    Adds keywords to the page, avoiding duplicates.
    """
    for word in keyword_list:
        keyword = db.query(Keyword).filter(Keyword.word == word).first()
        if not keyword:
            keyword = Keyword(word=word)
            db.add(keyword)
        if keyword not in page.keywords:
            page.keywords.append(keyword)

def recalculate_trust(db):
    pages = db.query(Page).all()
    page_map = {p.id: p for p in pages}
    for _ in range(10):
        new_trust_values = {}
        for p in pages:
            inbound_pages = p.incoming_links
            inbound_trust_sum = 0.0
            for inp in inbound_pages:
                out_degree = len(inp.linked_pages) if len(inp.linked_pages) > 0 else 1
                inbound_trust_sum += (inp.trust / out_degree)
            new_trust = (inbound_trust_sum * 0.9) + (p.trust * 0.1)
            new_trust_values[p.id] = new_trust

        for pid, new_val in new_trust_values.items():
            page = page_map[pid]
            page.trust = new_val
    db.commit()

def crawler_thread():
    while RUN_CRAWLER:
        with SessionLocal() as db:
            old_trusts = {p.id: p.trust for p in db.query(Page).all()}
            recalculate_trust(db)

            updated_pages = db.query(Page).all()
            for p in updated_pages:
                if p.trust > TRUST_THRESHOLD and not p.keywords:
                    old_trust = old_trusts.get(p.id, 0)
                    if p.trust > old_trust:
                        crawl_queue.put((-p.trust, p.id))

            while not crawl_queue.empty():
                trust, pid = crawl_queue.get()
                page = db.query(Page).filter(Page.id == pid).first()
                if not page or page.title:
                    continue
                title, keywords, found_links = fetch_page_data(page.url)
                if title:
                    page.title = title
                if keywords:
                    add_keywords_to_page(db, page, keywords)
                for link in found_links:
                    page.linked_pages.append(db.query(Page).filter(Page.url == link).first())
                db.commit()
                break
        time.sleep(1)

thread = threading.Thread(target=crawler_thread, daemon=True)
thread.start()
