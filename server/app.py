"""Sibawayh: self-hosted learning API and Arabic student/admin interfaces."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
DB_PATH = os.getenv('SIBAWAYH_DB', str(ROOT / 'sibawayh.db'))
PUBLIC_URL = os.getenv('PUBLIC_URL', 'http://127.0.0.1:8000').rstrip('/')
SECURE = PUBLIC_URL.startswith('https://')
DAY = 86400
GRADES = ['الأول الثانوي', 'الثاني الثانوي', 'الثالث الثانوي']
SUBJECTS = ['النحو', 'البلاغة', 'الأدب', 'النصوص', 'القراءة', 'التعبير']
app = FastAPI(title='سيبويه', docs_url=None, redoc_url=None, openapi_url=None)

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def initialize():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
          phone TEXT NOT NULL, grade TEXT NOT NULL, password TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'student', blocked INTEGER DEFAULT 0,
          created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
          csrf TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS courses (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL,
          grade TEXT NOT NULL, description TEXT DEFAULT '', published INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS lessons (
          id INTEGER PRIMARY KEY, course_id INTEGER REFERENCES courses(id),
          title TEXT NOT NULL, subject TEXT NOT NULL, provider TEXT NOT NULL,
          source TEXT NOT NULL, notes TEXT DEFAULT '', attachment TEXT DEFAULT '',
          position INTEGER DEFAULT 1, published INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS subscriptions (
          user_id INTEGER REFERENCES users(id), course_id INTEGER REFERENCES courses(id),
          expires INTEGER NOT NULL, PRIMARY KEY(user_id,course_id));
        CREATE TABLE IF NOT EXISTS progress (
          user_id INTEGER REFERENCES users(id), lesson_id INTEGER REFERENCES lessons(id),
          seconds REAL DEFAULT 0, completed INTEGER DEFAULT 0, updated INTEGER NOT NULL,
          PRIMARY KEY(user_id,lesson_id));
        CREATE TABLE IF NOT EXISTS playback_sessions (
          id TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id),
          lesson_id INTEGER REFERENCES lessons(id), created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY, actor INTEGER REFERENCES users(id),
          action TEXT NOT NULL, target TEXT NOT NULL, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, count INTEGER, until INTEGER);
        ''')
        for grade in GRADES:
            if not c.execute("SELECT 1 FROM courses WHERE grade=? AND kind='school'", (grade,)).fetchone():
                c.execute('INSERT INTO courses(title,kind,grade,description) VALUES(?,?,?,?)',
                          ('اللغة العربية — '+grade, 'school', grade, 'كل فروع اللغة العربية باشتراك واحد لمدة ٣٠ يومًا'))
        c.execute("INSERT OR IGNORE INTO settings VALUES('whatsapp','')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('announcement','أهلًا بك في منصة سيبويه للغة العربية')")

initialize()

def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex()+':'+digest.hex()

def verify_password(password, stored):
    salt, digest = stored.split(':')
    return hmac.compare_digest(hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex(), digest)

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def audit(c, actor, action, target):
    c.execute('INSERT INTO audit(actor,action,target,created) VALUES(?,?,?,?)', (actor, action, str(target), int(time.time())))

def auth(request, admin=False):
    raw = request.cookies.get('session', '')
    with db() as c:
        row = c.execute('SELECT u.*,s.csrf,s.token AS session_hash FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>?',
                        (token_hash(raw), int(time.time()))).fetchone()
    if not row or row['blocked']:
        raise HTTPException(401, 'سجّل الدخول للمتابعة')
    if admin and row['role'] != 'admin':
        raise HTTPException(403, 'هذه الصفحة للإدارة فقط')
    if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(request.headers.get('x-csrf-token',''), row['csrf']):
        raise HTTPException(403, 'انتهت صلاحية الطلب؛ حدّث الصفحة')
    return dict(row)

def safe_user(u):
    return {k:u[k] for k in ('id','name','email','phone','grade','role','blocked','created')}

def rate_limit(request, suffix, maximum=12):
    key = (request.client.host if request.client else 'local') + ':' + suffix
    now = int(time.time())
    denied = False
    with db() as c:
        r = c.execute('SELECT * FROM attempts WHERE key=?', (key,)).fetchone()
        if r and r['until'] > now:
            denied = r['count'] >= maximum
            c.execute('UPDATE attempts SET count=count+1 WHERE key=?', (key,))
        else:
            c.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)', (key,1,now+900))
        c.execute('DELETE FROM attempts WHERE until<?', (now-900,))
    if denied:
        raise HTTPException(429, 'محاولات كثيرة؛ جرّب بعد ١٥ دقيقة')

def create_session(response, user_id):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with db() as c:
        # One live student session. Admin logins remain independent.
        role = c.execute('SELECT role FROM users WHERE id=?',(user_id,)).fetchone()['role']
        if role == 'student':
            c.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
        c.execute('DELETE FROM sessions WHERE expires<?',(int(time.time()),))
        c.execute('INSERT INTO sessions VALUES(?,?,?,?)', (token_hash(token),user_id,csrf,int(time.time())+7*DAY))
    response.set_cookie('session', token, httponly=True, secure=SECURE, samesite='strict', max_age=7*DAY, path='/')
    return csrf

class Register(BaseModel):
    name: str = Field(min_length=3,max_length=100)
    email: str = Field(min_length=5,max_length=180)
    phone: str = Field(min_length=8,max_length=20)
    grade: str
    password: str = Field(min_length=10,max_length=128)

class Login(BaseModel):
    email: str = Field(max_length=180)
    password: str = Field(max_length=128)

@app.middleware('http')
async def security_headers(request, call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin != PUBLIC_URL:
            return Response('Invalid origin', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net https://www.youtube.com; style-src 'self'; img-src 'self' data:; connect-src 'self' https:; media-src 'self' https: blob:; frame-src https://www.youtube.com https://drive.google.com https://*.cloudflarestream.com https://iframe.videodelivery.net; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    if SECURE:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response

@app.post('/api/register')
def register(data:Register, request:Request, response:Response):
    rate_limit(request,'register:'+token_hash(data.email.strip().lower()),6)
    email=data.email.strip().lower()
    if data.grade not in GRADES or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or not re.fullmatch(r'\+?[0-9]{8,15}',data.phone):
        raise HTTPException(400,'راجع الصف والبريد ورقم الهاتف بالأرقام الإنجليزية')
    try:
        with db() as c:
            cur=c.execute('INSERT INTO users(name,email,phone,grade,password,created) VALUES(?,?,?,?,?,?)',
                          (data.name.strip(),email,data.phone,data.grade,password_hash(data.password),int(time.time())))
            uid=cur.lastrowid
            audit(c,uid,'register',uid)
    except sqlite3.IntegrityError:
        raise HTTPException(409,'البريد مسجل بالفعل؛ استخدم تسجيل الدخول')
    return {'csrf':create_session(response,uid)}

@app.post('/api/login')
def login(data:Login,request:Request,response:Response):
    rate_limit(request,'login:'+token_hash(data.email.strip().lower()),20)
    with db() as c:
        u=c.execute('SELECT * FROM users WHERE email=?',(data.email.strip().lower(),)).fetchone()
    if not u:
        verify_password(data.password,DUMMY_PASSWORD)
    if not u or not verify_password(data.password,u['password']) or u['blocked']:
        raise HTTPException(401,'بيانات الدخول غير صحيحة أو الحساب موقوف')
    return {'csrf':create_session(response,u['id'])}

DUMMY_PASSWORD=password_hash(secrets.token_urlsafe(24))

@app.post('/api/logout')
def logout(request:Request,response:Response):
    u=auth(request)
    with db() as c:
        c.execute('DELETE FROM sessions WHERE token=?',(u['session_hash'],))
    response.delete_cookie('session',path='/')
    return {'ok':True}

@app.get('/api/config')
def config():
    with db() as c:
        settings=dict(c.execute('SELECT key,value FROM settings').fetchall())
    return {'grades':GRADES,'subjects':SUBJECTS,**settings}

@app.get('/api/me')
def me(request:Request):
    u=auth(request)
    with db() as c:
        subs=[dict(r) for r in c.execute('SELECT s.course_id,s.expires,c.title,c.kind FROM subscriptions s JOIN courses c ON c.id=s.course_id WHERE user_id=?',(u['id'],))]
    return {'user':safe_user(u),'subscriptions':subs,'csrf':u['csrf'],'now':int(time.time())}

@app.get('/api/courses')
def courses(request:Request):
    u=auth(request)
    with db() as c:
        rows=c.execute("SELECT c.*,COALESCE(s.expires,0) expires,(SELECT COUNT(*) FROM lessons l WHERE l.course_id=c.id AND published=1) lesson_count FROM courses c LEFT JOIN subscriptions s ON s.course_id=c.id AND s.user_id=? WHERE c.published=1 AND (c.kind='develop' OR c.grade=?) ORDER BY c.id",(u['id'],u['grade'])).fetchall()
    return {'courses':[dict(r) for r in rows],'now':int(time.time())}

def entitlement(c,u,course_id):
    course=c.execute('SELECT * FROM courses WHERE id=?',(course_id,)).fetchone()
    if not course or not course['published']:
        raise HTTPException(404,'الكورس غير متاح')
    if u['role']=='admin':
        return
    if course['kind']=='school' and course['grade']!=u['grade']:
        raise HTTPException(403,'هذا الكورس لصف آخر')
    sub=c.execute('SELECT expires FROM subscriptions WHERE user_id=? AND course_id=?',(u['id'],course_id)).fetchone()
    if not sub or sub['expires']<=time.time():
        raise HTTPException(403,'الاشتراك غير مفعل أو انتهى؛ تواصل للتجديد')

@app.get('/api/courses/{course_id}/lessons')
def lessons(course_id:int,request:Request):
    u=auth(request)
    with db() as c:
        # Metadata can be browsed while locked; no source/attachment exposed.
        course=c.execute("SELECT * FROM courses WHERE id=? AND published=1 AND (kind='develop' OR grade=?)",(course_id,u['grade'])).fetchone()
        if not course and u['role']!='admin':
            raise HTTPException(404,'الكورس غير متاح')
        rows=c.execute('SELECT l.id,l.title,l.subject,l.position,COALESCE(p.seconds,0) seconds,COALESCE(p.completed,0) completed FROM lessons l LEFT JOIN progress p ON p.lesson_id=l.id AND p.user_id=? WHERE l.course_id=? AND l.published=1 ORDER BY l.position,l.id',(u['id'],course_id)).fetchall()
    return {'lessons':[dict(r) for r in rows]}

def https_url(value):
    p=urlparse(value)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.fragment or len(value)>3000:
        raise HTTPException(400,'استخدم رابط HTTPS صحيحًا')
    return value

def normalize_source(provider,source):
    source=source.strip()
    if provider=='youtube':
        if re.fullmatch(r'[A-Za-z0-9_-]{11}',source): return source
        p=urlparse(https_url(source))
        if p.hostname in ('youtube.com','www.youtube.com','m.youtube.com'):
            vid=parse_qs(p.query).get('v',[''])[0] if p.path=='/watch' else p.path.split('/')[-1]
        elif p.hostname=='youtu.be': vid=p.path.strip('/')
        else: vid=''
        if re.fullmatch(r'[A-Za-z0-9_-]{11}',vid): return vid
    elif provider=='drive':
        if re.fullmatch(r'[\w-]{15,200}',source): return source
        p=urlparse(https_url(source))
        if p.hostname=='drive.google.com':
            m=re.search(r'/file/d/([\w-]+)',p.path)
            vid=m.group(1) if m else parse_qs(p.query).get('id',[''])[0]
            if re.fullmatch(r'[\w-]{15,200}',vid): return vid
    elif provider=='stream':
        if re.fullmatch(r'[a-fA-F0-9]{32}',source): return source
    elif provider=='r2':
        if source and len(source)<=1000 and not source.startswith('/') and '\\' not in source and '..' not in source.split('/'):
            return source
    elif provider in ('mp4','hls'):
        return https_url(source)
    raise HTTPException(400,'رابط المصدر أو معرّف الفيديو غير صحيح')

@app.post('/api/lessons/{lesson_id}/play')
async def play(lesson_id:int,request:Request):
    u=auth(request)
    with db() as c:
        lesson=c.execute('SELECT * FROM lessons WHERE id=? AND published=1',(lesson_id,)).fetchone()
        if not lesson: raise HTTPException(404,'الدرس غير متاح')
        entitlement(c,u,lesson['course_id'])
        sid=secrets.token_hex(8)
        c.execute('INSERT INTO playback_sessions VALUES(?,?,?,?)',(sid,u['id'],lesson_id,int(time.time())))
        p=c.execute('SELECT seconds FROM progress WHERE user_id=? AND lesson_id=?',(u['id'],lesson_id)).fetchone()
    source,provider=lesson['source'],lesson['provider']
    if provider=='youtube':
        source='https://www.youtube.com/embed/'+source+'?playsinline=1&rel=0&enablejsapi=1&origin='+quote(PUBLIC_URL,safe='')
    elif provider=='drive': source='https://drive.google.com/file/d/'+source+'/preview'
    elif provider=='stream':
        account=os.getenv('CF_ACCOUNT_ID','')
        api_token=os.getenv('CF_STREAM_TOKEN','')
        if not re.fullmatch(r'[a-fA-F0-9]{32}',account) or not api_token:
            raise HTTPException(503,'مصدر Cloudflare Stream يحتاج إعداد مفاتيحه على الخادم')
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r=await client.post(f'https://api.cloudflare.com/client/v4/accounts/{account}/stream/{source}/token',
                    headers={'Authorization':'Bearer '+api_token},json={'exp':int(time.time())+4*3600})
                r.raise_for_status()
                source='https://iframe.videodelivery.net/'+r.json()['result']['token']
        except (httpx.HTTPError,KeyError,ValueError):
            raise HTTPException(502,'تعذر إصدار رابط مشاهدة Cloudflare؛ راجع الإعدادات')
    elif provider=='r2':
        account=os.getenv('CF_ACCOUNT_ID','')
        if not re.fullmatch(r'[a-fA-F0-9]{32}',account) or not all(os.getenv(k) for k in ('R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY','R2_BUCKET')):
            raise HTTPException(503,'مصدر R2 يحتاج إعداد مفاتيحه على الخادم')
        try:
            import boto3
            client=boto3.client('s3',endpoint_url=f'https://{account}.r2.cloudflarestorage.com',region_name='auto',
                aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'])
            source=client.generate_presigned_url('get_object',Params={'Bucket':os.environ['R2_BUCKET'],'Key':source},ExpiresIn=300)
        except Exception:
            raise HTTPException(502,'تعذر إصدار رابط R2؛ راجع الإعدادات')
    return {'id':lesson_id,'title':lesson['title'],'notes':lesson['notes'],'attachment':lesson['attachment'],
            'provider':provider,'url':source,'watermark':f'ST-{u["id"]:05d} • {sid}', 'playback_id':sid,
            'resume':p['seconds'] if p else 0,'expires_in':300}

@app.post('/api/playback/{sid}/refresh')
async def refresh_playback(sid:str,request:Request):
    u=auth(request)
    with db() as c:
        row=c.execute('SELECT lesson_id FROM playback_sessions WHERE id=? AND user_id=?',(sid,u['id'])).fetchone()
        if not row:raise HTTPException(404,'جلسة غير متاحة')
    return await play(row['lesson_id'],request)

@app.get('/api/playback/{sid}/check')
def check(sid:str,request:Request):
    u=auth(request)
    with db() as c:
        r=c.execute('SELECT p.*,l.course_id,l.published FROM playback_sessions p JOIN lessons l ON l.id=p.lesson_id WHERE p.id=? AND p.user_id=?',(sid,u['id'])).fetchone()
        if not r or not r['published']: raise HTTPException(403,'جلسة غير متاحة')
        entitlement(c,u,r['course_id'])
    return {'ok':True}

class Progress(BaseModel):
    seconds:float=Field(ge=0,le=86400,allow_inf_nan=False)
    completed:bool=False

@app.post('/api/lessons/{lesson_id}/progress')
def progress(lesson_id:int,data:Progress,request:Request):
    u=auth(request)
    with db() as c:
        l=c.execute('SELECT course_id FROM lessons WHERE id=? AND published=1',(lesson_id,)).fetchone()
        if not l:raise HTTPException(404,'الدرس غير متاح')
        entitlement(c,u,l['course_id'])
        c.execute('INSERT INTO progress VALUES(?,?,?,?,?) ON CONFLICT(user_id,lesson_id) DO UPDATE SET seconds=excluded.seconds,completed=MAX(progress.completed,excluded.completed),updated=excluded.updated',
                  (u['id'],lesson_id,data.seconds,int(data.completed),int(time.time())))
    return {'ok':True}

@app.get('/api/admin/students')
def students(request:Request):
    auth(request,True)
    with db() as c:
        rows=c.execute("SELECT id,name,email,phone,grade,blocked,created FROM users WHERE role='student' ORDER BY id DESC").fetchall()
        subs=[dict(r) for r in c.execute('SELECT * FROM subscriptions')]
    return {'students':[dict(r) for r in rows],'subscriptions':subs}

@app.get('/api/admin/content')
def content(request:Request):
    auth(request,True)
    with db() as c:
        return {'courses':[dict(r) for r in c.execute('SELECT * FROM courses')],
                'lessons':[dict(r) for r in c.execute('SELECT * FROM lessons ORDER BY position,id')]}

class Renew(BaseModel):
    user_id:int
    course_id:int
    days:int=Field(default=30,ge=1,le=365)
    request_id:str=Field(min_length=16,max_length=100)

@app.post('/api/admin/renew')
def renew(data:Renew,request:Request):
    u=auth(request,True)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        # Idempotency prevents duplicate renewal when a request is retried.
        duplicate=c.execute("SELECT target FROM audit WHERE action='renew' AND json_extract(target,'$.request_id')=?",(data.request_id,)).fetchone()
        if duplicate:return {'ok':True,'expires':json.loads(duplicate['target'])['expires']}
        student=c.execute("SELECT * FROM users WHERE id=? AND role='student'",(data.user_id,)).fetchone()
        course=c.execute('SELECT * FROM courses WHERE id=?',(data.course_id,)).fetchone()
        if not student or not course:raise HTTPException(404,'الطالب أو الكورس غير موجود')
        if course['kind']=='school' and course['grade']!=student['grade']:raise HTTPException(400,'الصف غير مطابق')
        if course['kind']=='school' and data.days!=30:raise HTTPException(400,'الاشتراك الدراسي مدته ٣٠ يومًا')
        old=c.execute('SELECT expires FROM subscriptions WHERE user_id=? AND course_id=?',(data.user_id,data.course_id)).fetchone()
        expires=max(int(time.time()),old['expires'] if old else 0)+data.days*DAY
        c.execute('INSERT OR REPLACE INTO subscriptions VALUES(?,?,?)',(data.user_id,data.course_id,expires))
        audit(c,u['id'],'renew',json.dumps({'request_id':data.request_id,'student':data.user_id,'course':data.course_id,'expires':expires}))
    return {'ok':True,'expires':expires}

class StudentUpdate(BaseModel):
    user_id:int
    blocked:bool

@app.post('/api/admin/student')
def block(data:StudentUpdate,request:Request):
    u=auth(request,True)
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=? AND role='student'",(data.user_id,)).fetchone():raise HTTPException(404,'الطالب غير موجود')
        c.execute("UPDATE users SET blocked=? WHERE id=? AND role='student'",(int(data.blocked),data.user_id))
        if data.blocked:c.execute('DELETE FROM sessions WHERE user_id=?',(data.user_id,))
        audit(c,u['id'],'block' if data.blocked else 'unblock',data.user_id)
    return {'ok':True}

class Revoke(BaseModel):
    user_id:int
    course_id:int

@app.post('/api/admin/revoke')
def revoke(data:Revoke,request:Request):
    u=auth(request,True)
    with db() as c:
        c.execute('DELETE FROM subscriptions WHERE user_id=? AND course_id=?',(data.user_id,data.course_id))
        audit(c,u['id'],'revoke',f'{data.user_id}:{data.course_id}')
    return {'ok':True}

class CourseInput(BaseModel):
    id:int|None=None
    title:str=Field(min_length=2,max_length=150)
    kind:str
    grade:str=''
    description:str=Field(default='',max_length=2000)
    published:bool=True

@app.post('/api/admin/course')
def save_course(data:CourseInput,request:Request):
    u=auth(request,True)
    if data.kind not in ('school','develop') or (data.kind=='school' and data.grade not in GRADES):raise HTTPException(400,'نوع الكورس أو الصف غير صحيح')
    with db() as c:
        if data.kind=='school':
            existing=c.execute("SELECT id FROM courses WHERE kind='school' AND grade=? AND id!=?",(data.grade,data.id or 0)).fetchone()
            if existing:raise HTTPException(400,'لكل صف كورس دراسي واحد يضم جميع الأقسام')
        if data.id:
            old=c.execute('SELECT kind,grade FROM courses WHERE id=?',(data.id,)).fetchone()
            if not old:raise HTTPException(404,'الكورس غير موجود')
            if old['kind']!=data.kind or old['grade']!=data.grade:raise HTTPException(400,'نوع وصف الكورس ثابتان؛ أنشئ كورسًا مستقلًا')
            c.execute('UPDATE courses SET title=?,description=?,published=? WHERE id=?',(data.title,data.description,int(data.published),data.id))
            cid=data.id
        else:
            cid=c.execute('INSERT INTO courses(title,kind,grade,description,published) VALUES(?,?,?,?,?)',(data.title,data.kind,data.grade,data.description,int(data.published))).lastrowid
        audit(c,u['id'],'course',cid)
    return {'id':cid}

class LessonInput(BaseModel):
    id:int|None=None
    course_id:int
    title:str=Field(min_length=2,max_length=180)
    subject:str
    provider:str
    source:str=Field(min_length=1,max_length=3000)
    notes:str=Field(default='',max_length=10000)
    attachment:str=Field(default='',max_length=3000)
    position:int=Field(default=1,ge=1,le=10000)
    published:bool=True

@app.post('/api/admin/lesson')
def save_lesson(data:LessonInput,request:Request):
    u=auth(request,True)
    if data.subject not in SUBJECTS+['طور نفسك']:raise HTTPException(400,'القسم غير صحيح')
    source=normalize_source(data.provider,data.source)
    if data.attachment:https_url(data.attachment)
    vals=(data.course_id,data.title,data.subject,data.provider,source,data.notes,data.attachment,data.position,int(data.published))
    with db() as c:
        if not c.execute('SELECT 1 FROM courses WHERE id=?',(data.course_id,)).fetchone():raise HTTPException(404,'الكورس غير موجود')
        if data.id:
            if not c.execute('SELECT 1 FROM lessons WHERE id=?',(data.id,)).fetchone():raise HTTPException(404,'الدرس غير موجود')
            c.execute('UPDATE lessons SET course_id=?,title=?,subject=?,provider=?,source=?,notes=?,attachment=?,position=?,published=? WHERE id=?',vals+(data.id,))
            lid=data.id
        else:lid=c.execute('INSERT INTO lessons(course_id,title,subject,provider,source,notes,attachment,position,published) VALUES(?,?,?,?,?,?,?,?,?)',vals).lastrowid
        audit(c,u['id'],'lesson',lid)
    return {'id':lid}

class SettingsInput(BaseModel):
    whatsapp:str=Field(max_length=20)
    announcement:str=Field(max_length=1000)

@app.post('/api/admin/settings')
def settings(data:SettingsInput,request:Request):
    u=auth(request,True)
    if data.whatsapp and not re.fullmatch(r'[1-9][0-9]{7,14}',data.whatsapp):raise HTTPException(400,'رقم واتساب دولي بدون + أو مسافات، مثل 201xxxxxxxxx')
    with db() as c:
        for k,v in data.model_dump().items():c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(k,v))
        audit(c,u['id'],'settings','updated')
    return {'ok':True}

@app.get('/api/admin/activity')
def activity(request:Request):
    auth(request,True)
    with db() as c:
        return {'audit':[dict(r) for r in c.execute('SELECT id,actor,action,target,created FROM audit ORDER BY id DESC LIMIT 200')],
                'progress':[dict(r) for r in c.execute('SELECT p.*,u.name,u.email,l.title FROM progress p JOIN users u ON u.id=p.user_id JOIN lessons l ON l.id=p.lesson_id ORDER BY updated DESC LIMIT 200')],
                'playback':[dict(r) for r in c.execute('SELECT p.*,u.email,l.title FROM playback_sessions p JOIN users u ON u.id=p.user_id JOIN lessons l ON l.id=p.lesson_id ORDER BY created DESC LIMIT 200')]}

class SecurityEvent(BaseModel):
    reason:str

@app.post('/api/security-event')
def security_event(data:SecurityEvent,request:Request):
    u=auth(request)
    if data.reason not in ('vpn','proxy','headphones_disconnected'):raise HTTPException(400,'سبب غير صحيح')
    rate_limit(request,'security_event',120)
    with db() as c:audit(c,u['id'],'device_report',data.reason)
    return {'ok':True}

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.get('/health')
def health():return {'ok':True}

@app.get('/')
@app.get('/admin')
@app.get('/watch/{lesson_id}')
def page():return FileResponse(ROOT/'static'/'index.html')

if __name__=='__main__':
    import argparse,getpass
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['create-admin'])
    parser.add_argument('--email',required=True)
    parser.add_argument('--name',default='إدارة سيبويه')
    args=parser.parse_args()
    pw=getpass.getpass('Admin password (at least 12 characters): ')
    if len(pw)<12:raise SystemExit('Use at least 12 characters')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',args.email.strip()):raise SystemExit('Invalid email')
    with db() as c:
        if c.execute('SELECT 1 FROM users WHERE email=?',(args.email.strip().lower(),)).fetchone():raise SystemExit('Email already exists; no account modified')
        c.execute("INSERT INTO users(name,email,phone,grade,password,role,created) VALUES(?,?,?,?,?,'admin',?)",(args.name,args.email.strip().lower(),'',GRADES[0],password_hash(pw),int(time.time())))
    print('Admin created. Open /admin and sign in.')
