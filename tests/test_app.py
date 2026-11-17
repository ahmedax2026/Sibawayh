import os
import tempfile
import time
import unittest
from fastapi.testclient import TestClient
TMP=tempfile.TemporaryDirectory()
os.environ['SIBAWAYH_DB']=os.path.join(TMP.name,'test.db')
from server import app as m

class AcademyTest(unittest.TestCase):
    def setUp(self):
        with m.db() as c:
            for t in ['audit','playback_sessions','progress','subscriptions','sessions','lessons','courses','users','attempts']:c.execute('DELETE FROM '+t)
        m.initialize()
        with m.db() as c:
            self.admin_id=c.execute("INSERT INTO users(name,email,phone,grade,password,role,created) VALUES(?,?,?,?,?,'admin',?)",('Admin','admin@example.com','',m.GRADES[0],m.password_hash('admin-password-123'),int(time.time()))).lastrowid
        self.admin=TestClient(m.app);self.student=TestClient(m.app)
        self.admin_csrf=self.admin.post('/api/login',json={'email':'admin@example.com','password':'admin-password-123'}).json()['csrf']
        r=self.student.post('/api/register',json={'name':'أحمد محمد','email':'student@example.com','phone':'01012345678','grade':m.GRADES[0],'password':'student-password-123'})
        self.assertEqual(r.status_code,200,r.text);self.csrf=r.json()['csrf']
        self.student_id=self.student.get('/api/me').json()['user']['id']
        self.school=self.admin.get('/api/admin/content').json()['courses'][0]['id']
        self.lesson=self.add_lesson(self.school)
    def tearDown(self):self.admin.close();self.student.close()
    def apost(self,path,data):return self.admin.post('/api'+path,json=data,headers={'X-CSRF-Token':self.admin_csrf})
    def spost(self,path,data):return self.student.post('/api'+path,json=data,headers={'X-CSRF-Token':self.csrf})
    def add_lesson(self,course,provider='youtube',source='https://www.youtube.com/watch?v=dQw4w9WgXcQ'):
        r=self.apost('/admin/lesson',{'course_id':course,'title':'الجملة الاسمية','subject':'النحو','provider':provider,'source':source})
        self.assertEqual(r.status_code,200,r.text);return r.json()['id']
    def renew(self,course=None,rid='test-request-0000001'):
        r=self.apost('/admin/renew',{'user_id':self.student_id,'course_id':course or self.school,'request_id':rid})
        self.assertEqual(r.status_code,200,r.text);return r.json()['expires']
    def test_unpaid_no_source_or_play(self):
        self.assertEqual(self.spost(f'/lessons/{self.lesson}/play',{}).status_code,403)
        data=self.student.get(f'/api/courses/{self.school}/lessons').json()
        self.assertNotIn('source',str(data));self.assertNotIn('dQw4w9WgXcQ',str(data))
        self.assertEqual(self.student.get('/api/admin/content').status_code,403)
    def test_renewal_30_days_and_idempotency(self):
        start=int(time.time());expiry=self.renew()
        self.assertLess(abs(expiry-start-30*m.DAY),3)
        self.assertEqual(self.renew(),expiry)
        self.assertEqual(self.renew(rid='test-request-0000002'),expiry+30*m.DAY)
        with m.db() as c:c.execute('UPDATE subscriptions SET expires=?',(start-100,))
        new=self.renew(rid='test-request-0000003');self.assertLess(abs(new-int(time.time())-30*m.DAY),3)
    def test_expiry_and_revocation_during_play(self):
        self.renew();p=self.spost(f'/lessons/{self.lesson}/play',{}).json()
        self.assertIn('youtube.com/embed/',p['url']);self.assertIn('ST-',p['watermark'])
        path='/api/playback/'+p['playback_id']+'/check'
        self.assertEqual(self.student.get(path).status_code,200)
        self.apost('/admin/revoke',{'user_id':self.student_id,'course_id':self.school})
        self.assertEqual(self.student.get(path).status_code,403)
        self.assertEqual(self.spost(f'/lessons/{self.lesson}/play',{}).status_code,403)
    def test_develop_independent_from_school(self):
        course=self.apost('/admin/course',{'title':'طور نفسك في الإجازة','kind':'develop'}).json()['id'];lesson=self.add_lesson(course)
        self.renew();self.assertEqual(self.spost(f'/lessons/{lesson}/play',{}).status_code,403)
        self.renew(course=course,rid='develop-request-00001');self.assertEqual(self.spost(f'/lessons/{lesson}/play',{}).status_code,200)
        self.apost('/admin/revoke',{'user_id':self.student_id,'course_id':self.school})
        self.assertEqual(self.spost(f'/lessons/{lesson}/play',{}).status_code,200)
    def test_grade_is_enforced_server_side(self):
        course=self.admin.get('/api/admin/content').json()['courses'][1]['id'];lesson=self.add_lesson(course)
        r=self.apost('/admin/renew',{'user_id':self.student_id,'course_id':course,'request_id':'wrong-grade-request'})
        self.assertEqual(r.status_code,400);self.assertEqual(self.spost(f'/lessons/{lesson}/play',{}).status_code,403)
    def test_csrf_origin_and_role(self):
        self.assertEqual(self.student.post('/api/logout',json={}).status_code,403)
        self.assertEqual(self.spost('/admin/settings',{'whatsapp':'201012345678','announcement':'x'}).status_code,403)
        self.assertEqual(self.student.post('/api/logout',json={},headers={'Origin':'https://evil.example','X-CSRF-Token':self.csrf}).status_code,403)
    def test_one_student_session_and_block(self):
        other=TestClient(m.app)
        try:
            r=other.post('/api/login',json={'email':'student@example.com','password':'student-password-123'});self.assertEqual(r.status_code,200)
            self.assertEqual(self.student.get('/api/me').status_code,401)
            self.apost('/admin/student',{'user_id':self.student_id,'blocked':True});self.assertEqual(other.get('/api/me').status_code,401)
        finally:other.close()
    def test_progress_not_downgraded_and_no_progress_unpaid(self):
        self.assertEqual(self.spost(f'/lessons/{self.lesson}/progress',{'seconds':20}).status_code,403)
        self.renew();self.assertEqual(self.spost(f'/lessons/{self.lesson}/progress',{'seconds':50,'completed':True}).status_code,200)
        self.spost(f'/lessons/{self.lesson}/progress',{'seconds':10,'completed':False})
        l=self.student.get(f'/api/courses/{self.school}/lessons').json()['lessons'][0]
        self.assertEqual(l['completed'],1);self.assertEqual(l['seconds'],10)
    def test_provider_validation(self):
        self.assertEqual(m.normalize_source('drive','https://drive.google.com/file/d/abcdefghijklmno12345/view'),'abcdefghijklmno12345')
        for provider,value in [('youtube','https://evil.example/watch?v=dQw4w9WgXcQ'),('drive','https://evil.example/file/d/abcdefghijklmno12345/view'),('mp4','http://example.com/a.mp4'),('r2','../secret')]:
            with self.assertRaises(m.HTTPException):m.normalize_source(provider,value)
        self.add_lesson(self.school,'mp4','https://media.example.com/lesson.mp4')
    def test_source_setup_errors_are_explicit(self):
        stream=self.add_lesson(self.school,'stream','a'*32);self.renew()
        self.assertEqual(self.spost(f'/lessons/{stream}/play',{}).status_code,503)
    def test_registration_cannot_inject_admin_role(self):
        client=TestClient(m.app)
        try:
            r=client.post('/api/register',json={'name':'New student','email':'other@example.com','phone':'01012345679','grade':m.GRADES[0],'password':'student-password-123','role':'admin'})
            self.assertEqual(r.status_code,200);self.assertEqual(client.get('/api/me').json()['user']['role'],'student')
        finally:client.close()
    def test_password_not_plaintext_and_secure_headers(self):
        with m.db() as c:stored=c.execute('SELECT password FROM users WHERE id=?',(self.student_id,)).fetchone()['password']
        self.assertNotIn('student-password',stored)
        r=self.student.get('/');self.assertEqual(r.headers['x-frame-options'],'DENY');self.assertIn("frame-ancestors 'none'",r.headers['content-security-policy'])
        r=self.student.post('/api/login',json={'email':'student@example.com','password':'student-password-123'})
        self.assertIn('HttpOnly',r.headers['set-cookie']);self.assertIn('SameSite=strict',r.headers['set-cookie'])

if __name__=='__main__':unittest.main()
