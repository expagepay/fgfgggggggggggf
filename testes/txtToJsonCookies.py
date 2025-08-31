import http.cookiejar as cookielib
import json

cookie_jar = cookielib.MozillaCookieJar("gg.txt")
cookie_jar.load(ignore_discard=True, ignore_expires=True)

cookies = []
for c in cookie_jar:
    cookies.append({
        "name": c.name,
        "value": c.value,
        "domain": c.domain,
        "path": c.path,
        "expires": c.expires,
        "httpOnly": c.get_nonstandard_attr('httponly') or False,
        "secure": c.secure,
        "sameSite": "Lax"  # valor default (pode ajustar se precisar)
    })

with open("cookies.json", "w") as f:
    json.dump(cookies, f, indent=2)