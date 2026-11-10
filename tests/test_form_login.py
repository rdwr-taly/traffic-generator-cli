"""Cookie-session (HTML form) logins, modelled on Hackazon's /user/login.

Hackazon answers a good login with 302 -> /account and a bad one by
re-rendering the form with 200. It sets PHPSESSID on every page, so neither
a 200 nor a cookie proves a login worked -- only the redirect does. The real
app was checked against razor29/hackazon-php84:v1.0.0.
"""

import asyncio
import secrets

from aiohttp import web
from aiohttp.test_utils import TestServer

from traffic_generator import (
    ContainerConfig,
    Metrics,
    SimulatedUser,
    SiteMap,
    TrafficGenerator,
)

GOOD = {"username": "test_user", "password": "123456"}
XFF = {"X-Forwarded-For": "203.0.113.9"}


def fake_hackazon(bad_login="rerender"):
    """bad_login: 'rerender' (Hackazon: 200 + form) or 'redirect_to_login'."""
    sessions = set()
    served_logged_in = []  # member pages actually rendered for a session

    def page(text, request):
        resp = web.Response(text=f"<html>{text}</html>", content_type="text/html")
        if "PHPSESSID" not in request.cookies:
            resp.set_cookie("PHPSESSID", secrets.token_hex(8))
        return resp

    def logged_in(request):
        return request.cookies.get("PHPSESSID") in sessions

    async def login(request):
        form = await request.post()
        if form.get("username") == GOOD["username"] and (
            form.get("password") == GOOD["password"]
        ):
            sid = secrets.token_hex(8)
            sessions.add(sid)
            resp = web.HTTPFound("/account")
            resp.set_cookie("PHPSESSID", sid)
            raise resp
        if bad_login == "redirect_to_login":
            raise web.HTTPFound("/user/login?error=1")
        return page("Username or password are incorrect.", request)

    async def login_form(request):
        return page("Login", request)

    def members_only(title):
        async def handler(request):
            if not logged_in(request):
                raise web.HTTPFound(f"/user/login?return_url={request.path}")
            served_logged_in.append(request.path)
            return page(title, request)

        return handler

    app = web.Application()
    app.router.add_post("/user/login", login)
    app.router.add_get("/user/login", login_form)
    app.router.add_get("/account", members_only("My Account"))
    app.router.add_get("/account/orders", members_only("My Orders"))
    app["served_logged_in"] = served_logged_in
    return app


def make_generator(base_url, credentials=None):
    cfg = ContainerConfig(
        **{
            "Traffic Generator URL": base_url,
            "XFF Header Name": "X-Forwarded-For",
            "Rate Limit": 1,
            "Simulated Users": 1,
            "Minimum Session Length": 1,
            "Maximum Session Length": 1,
        }
    )
    sm = SiteMap(
        has_auth=True,
        auth={
            "auth_type": "body_params",
            "auth_method": "POST",
            "auth_path": "/user/login",
            "credentials": {"body_params": credentials or GOOD},
        },
        paths=[],
        paths_auth_req=[
            {"method": "GET", "paths": ["/account/orders"], "traffic_type": "web"}
        ],
    )
    return TrafficGenerator(cfg, sm, Metrics())


def login_result(host, credentials=None, bad_login="rerender"):
    """What perform_authentication returns against the fake app at ``host``."""

    async def run():
        server = TestServer(fake_hackazon(bad_login), host="127.0.0.1")
        await server.start_server()
        try:
            g = make_generator(f"http://{host}:{server.port}", credentials)
            async with g.create_session() as session:
                return await g.perform_authentication(session, dict(XFF))
        finally:
            await server.close()

    return asyncio.run(run())


# --- success is read from the redirect, not from a JSON token ---------------
# Hostname target, so these hold whatever the cookie jar does with IPs.


def test_form_login_that_redirects_away_is_authenticated():
    assert login_result("localhost") is not None


def test_form_login_rerendered_with_200_is_not_authenticated():
    # Hackazon's wrong-password answer. Treating any non-JSON 2xx as success
    # would log every simulated user "in" here.
    bad = {"username": "test_user", "password": "wrong"}
    assert login_result("localhost", credentials=bad) is None


def test_form_login_redirected_back_to_login_page_is_not_authenticated():
    # The other common failure shape (e.g. Spring's /login?error).
    bad = {"username": "test_user", "password": "wrong"}
    assert login_result("localhost", bad, bad_login="redirect_to_login") is None


# --- IP targets (the Alteon tasks) must keep the session cookie -------------


def test_session_keeps_cookies_from_an_ip_target():
    async def run():
        app = web.Application()

        async def set_cookie(request):
            resp = web.Response(text="ok")
            resp.set_cookie("PHPSESSID", "abc123")
            return resp

        async def echo(request):
            return web.Response(text=request.cookies.get("PHPSESSID", "<none>"))

        app.router.add_get("/set", set_cookie)
        app.router.add_get("/echo", echo)
        server = TestServer(app, host="127.0.0.1")
        await server.start_server()
        try:
            g = make_generator(f"http://127.0.0.1:{server.port}")
            async with g.create_session() as session:
                await (await session.get(server.make_url("/set"))).read()
                return await (await session.get(server.make_url("/echo"))).text()
        finally:
            await server.close()

    assert asyncio.run(run()) == "abc123"


def test_logged_in_user_on_ip_target_gets_members_only_page():
    # End to end: log in, then the generator's own request to the sitemap's
    # paths_auth_req must land logged in (200), not bounce to the login page.
    async def run():
        app = fake_hackazon()
        server = TestServer(app, host="127.0.0.1")
        await server.start_server()
        try:
            g = make_generator(f"http://127.0.0.1:{server.port}")
            async with g.create_session() as session:
                token = await g.perform_authentication(session, dict(XFF))
                user = SimulatedUser(is_authenticated=token is not None)
                user.auth_token = token
                await g.perform_request(
                    session,
                    dict(XFF),
                    dict(g.headers_web_options[0]),
                    g.user_agents_web[0],
                    dict(g.headers_api_options[0]),
                    g.user_agents_api[0],
                    user,
                )
                return app["served_logged_in"]
        finally:
            await server.close()

    # Asserted on what the server rendered, not the generator's status count:
    # redirects are followed, so an anonymous bounce also ends in a 200 (on
    # the login form).
    assert asyncio.run(run()) == ["/account", "/account/orders"]
