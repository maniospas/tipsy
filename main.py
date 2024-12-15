# uvicorn main:app --reload

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from src.database import *


app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def search_form(request: Request):
    return templates.TemplateResponse("search.html", {"request": request})

@app.post("/search", response_class=HTMLResponse)
def search(request: Request, q: str = Form(...)):
    keywords = [k.strip().lower() for k in q.split() if k.strip()]

    db = SessionLocal()
    query = db.query(Page)
    for keyword in keywords:
        query = query.filter(Page.keywords.like(f"%{keyword}%"))
    results = query.order_by(Page.trust.desc()).all()
    db.close()

    return templates.TemplateResponse(
        "search.html", {"request": request, "results": results, "query": q}
    )

@app.get("/crawl", response_class=HTMLResponse)
def crawl_form(request: Request):
    db = SessionLocal()
    total_crawled_pages = db.query(Page).count()
    links_waiting = crawl_queue.qsize()
    db.close()
    return templates.TemplateResponse(
        "crawl.html",
        {
            "request": request,
            "total_crawled_pages": total_crawled_pages,
            "links_waiting": links_waiting,
        },
    )

@app.get("/crawl/status", response_class=JSONResponse)
def crawl_status():
    db = SessionLocal()
    total_crawled_pages = db.query(Page).count()
    links_waiting = crawl_queue.qsize()
    db.close()
    return {"total_crawled_pages": total_crawled_pages, "links_waiting": links_waiting}


@app.post("/crawl", response_class=HTMLResponse)
def add_page(request: Request, url: str = Form(...)):
    db = SessionLocal()
    page = db.query(Page).filter(Page.url == url).first()
    if not page:
        title, keywords, links = fetch_page_data(url)
        page = Page(url=url, trust=1.0, title=title)
        add_keywords_to_page(db, page, keywords)
        for link in links:
            page.linked_pages.append(db.query(Page).filter(Page.url == link).first())
        db.add(page)
        db.commit()
        message = "Page added successfully."
    elif page.trust != 1.0:
        if not page.title:
            title, keywords, links = fetch_page_data(url)
            page.title = title
            add_keywords_to_page(db, page, keywords)
            for link in links:
                page.linked_pages.append(db.query(Page).filter(Page.url == link).first())
        page.trust = 1.0
        db.commit()
        message = "Page already discovered by a previous task. It is now trusted."
    else:
        message = "Page already exists in the database."
    db.close()
    return templates.TemplateResponse("crawl.html", {"request": request, "message": message})

