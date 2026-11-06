import sys, time
sys.path.insert(0, sys.path[0])
from rec import Browser, Recorder, django

b = Browser()

# Portal - a phone, Anna's own account
b.viewport(390, 844, scale=2, mobile=True)
token = django("from datetime import timedelta; from django.utils import timezone; "
               "from coffee.models import Member, MemberLinkToken; "
               "print(MemberLinkToken.objects.create(member=Member.objects.get(nickname='Anna B.'), "
               "expires_at=timezone.now() + timedelta(minutes=5)).token)")[0]
b.go(f"/portal/link/{token}/")
time.sleep(1)
r = Recorder("portal")
time.sleep(2.5)
b.scroll(500, duration=1.5)
time.sleep(1.0)
b.go("/portal/stats/")
time.sleep(2.5)
b.scroll(700, duration=2.0)
time.sleep(1.5)
b.go("/portal/pay/")
time.sleep(3)
r.stop()

# Dashboard
b.viewport(1280, 800)
b.go("/dashboard/login/")
b.js("document.querySelector('input[name=username]').value='admin';"
     "document.querySelector('input[name=password]').value='demo-video-1';"
     "document.querySelector('form').submit()")
b.page.collect(0.5, until="Page.loadEventFired")
b.go("/dashboard/")
time.sleep(1.5)
r = Recorder("dashboard")
time.sleep(2.5)
b.go("/dashboard/statistics/")
time.sleep(2.5)
b.scroll(900, duration=2.5)
time.sleep(1.5)
b.go("/dashboard/members/")
time.sleep(3)
r.stop()
