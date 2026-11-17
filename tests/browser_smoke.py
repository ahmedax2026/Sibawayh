"""Real browser workflow check. Run separately after installing playwright+chromium."""
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE=Path(__file__).resolve().parents[1]
OUT=HERE/'browser-results'
OUT.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    os.environ['SIBAWAYH_DB']=str(Path(tmp)/'browser.db')
    os.environ['PUBLIC_URL']='http://127.0.0.1:8877'
    sys.path.insert(0,str(HERE))
    from server import app as m
    admin_password=secrets.token_urlsafe(20)
    student_password=secrets.token_urlsafe(20)
    with m.db() as c:
        c.execute("INSERT INTO users(name,email,phone,grade,password,role,created) VALUES(?,?,?,?,?,'admin',?)",('إدارة سيبويه','admin@example.test','',m.GRADES[0],m.password_hash(admin_password),int(time.time())))
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','server.app:app','--port','8877'],cwd=HERE,env=os.environ.copy(),stdout=subprocess.DEVNULL)
    try:
        import urllib.request
        for _ in range(100):
            try:urllib.request.urlopen('http://127.0.0.1:8877/health',timeout=1);break
            except Exception:time.sleep(.1)
        with sync_playwright() as p:
            kwargs={'headless':True}
            if os.getenv('CHROMIUM_PATH'):kwargs['executable_path']=os.environ['CHROMIUM_PATH']
            browser=p.chromium.launch(**kwargs)
            student_context=browser.new_context(viewport={'width':390,'height':844})
            page=student_context.new_page();errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8877',wait_until='networkidle')
            page.get_by_role('button',name='ابدأ رحلتك').click()
            page.locator('#name').fill('أحمد طالب التجربة')
            page.locator('#phone').fill('01012345678')
            page.locator('#email').fill('student@example.test')
            page.locator('#password').fill(student_password)
            page.get_by_role('button',name='إنشاء الحساب',exact=True).click()
            page.get_by_role('heading',name='حسابك في انتظار التفعيل').wait_for()
            page.screenshot(path=str(OUT/'01-student-pending.png'),full_page=True)
            admin_context=browser.new_context(viewport={'width':1440,'height':1000})
            admin=admin_context.new_page();admin.on('pageerror',lambda e:errors.append(str(e)))
            admin.goto('http://127.0.0.1:8877/admin')
            admin.locator('#email').fill('admin@example.test');admin.locator('#password').fill(admin_password)
            admin.get_by_role('button',name='دخول',exact=True).click()
            admin.locator('#renewstudent').wait_for()
            admin.locator('#renewstudent').select_option(index=1)
            admin.get_by_role('button',name='تأكيد الدفع وتفعيل الاشتراك').click()
            admin.get_by_text('تم الحفظ بنجاح',exact=True).wait_for()
            admin.screenshot(path=str(OUT/'02-admin-students.png'),full_page=True)
            admin.get_by_role('button',name='الكورسات والدروس',exact=True).click()
            f=admin.locator('#lessonform')
            f.locator('[name=title]').fill('الدرس الأول: الجملة الاسمية')
            f.locator('[name=source]').fill('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
            f.locator('[name=notes]').fill('شرح المبتدأ والخبر مع أمثلة وتدريبات.')
            f.get_by_role('button',name='حفظ المحاضرة',exact=True).click()
            admin.get_by_role('heading',name='الدرس الأول: الجملة الاسمية',exact=True).wait_for()
            # Update does not accidentally create a second lesson.
            admin.locator('[data-editlesson]').first.click()
            admin.locator('#lessonform [name=notes]').fill('ملاحظات معدلة')
            admin.locator('#lessonform').get_by_role('button',name='حفظ المحاضرة',exact=True).click()
            admin.wait_for_timeout(500)
            with m.db() as c:assert c.execute('SELECT COUNT(*) FROM lessons').fetchone()[0]==1
            page.reload(wait_until='networkidle')
            page.get_by_role('button',name='افتح الدروس',exact=True).wait_for()
            page.screenshot(path=str(OUT/'03-student-active.png'),full_page=True)
            page.get_by_role('button',name='افتح الدروس',exact=True).click()
            page.get_by_role('link',name='شاهد ▷').click()
            page.locator('#lessonframe').wait_for()
            assert 'youtube.com/embed/' in page.locator('#lessonframe').get_attribute('src')
            page.get_by_role('button',name='أتممت الدرس ✓').click()
            page.get_by_text('تم حفظ إتمام الدرس',exact=True).wait_for()
            with m.db() as c:c.execute('UPDATE subscriptions SET expires=?',(int(time.time())-1,))
            page.get_by_text('توقفت المشاهدة.',exact=False).wait_for(timeout=22000)
            assert not errors,errors
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal overflow on mobile'
            browser.close()
            print('PASS: registration, pending, admin activation, lesson create/edit, entitled playback, progress and expiry. No JS exceptions.')
    finally:
        proc.terminate();proc.wait(timeout=5)
