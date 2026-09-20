(() => {
  "use strict";

  const transport = window.fetch.bind(window);
  const safeMethods = new Set(["GET", "HEAD", "OPTIONS"]);
  let session = null;
  let sessionRequest = null;
  let contextGeneration = 0;
  const responseGenerations = new WeakMap();
  let notice = "";
  let workspaces = null;
  let workspaceRequest = null;
  let workspaceId = null;
  let resumeInNewTab = false;
  let switchingActor = false;
  const text = (zh, en) => document.documentElement.lang === "en" ? en : zh;
  const node = (id) => document.getElementById(id);
  const localRoles = value => value?.identity_source === "controlled-local-session";
  const actorName = actor => text(actor?.display_name || actor?.label || actor?.actor_id || "",
    actor?.display_name_en || actor?.label_en || actor?.actor_id || "");

  function roleNames(roles) {
    const names = {
      reader: ["查看", "Read"], operator: ["提出变更", "Propose"],
      approver: ["审批", "Approve"], executor: ["执行", "Execute"],
      governor: ["能力治理", "Govern skills"], administrator: ["管理", "Administer"],
    };
    return (roles || []).map(role => names[role] ? text(...names[role]) : role).join(" · ");
  }

  function setSession(next) {
    const identity = value => value ? JSON.stringify([value.mode, value.identity_source, value.authenticated,
      value.principal?.issuer, value.principal?.subject, value.principal?.tenant_id,
      value.principal?.actor_id, value.csrf_token]) : null;
    if (identity(session) !== identity(next)) {
      ++contextGeneration;
      workspaces = null;
      workspaceRequest = null;
      workspaceId = null;
    }
    session = next;
  }

  function contextChanged() {
    const error = new Error("WORKSPACE_REQUEST_CONTEXT_CHANGED");
    error.code = "WORKSPACE_REQUEST_CONTEXT_CHANGED";
    return error;
  }

  function requireContext(generation) {
    if (generation !== contextGeneration) throw contextChanged();
  }

  function emit() {
    renderSession();
    window.dispatchEvent(new CustomEvent("orgrebase:sessionchange", { detail: session }));
  }

  function renderSession() {
    const panel = node("workspace-session");
    if (!panel) return;
    const required = session && session.authentication_required === true;
    const locked = required && session.authenticated !== true;
    document.body.classList.toggle("session-locked", Boolean(locked));
    panel.dataset.state = !session ? "unavailable" : locked ? "signed-out" : "ready";
    panel.setAttribute("aria-label", text("登录会话", "Session"));
    for (const [id, zh, en] of [
      ["session-workspace-label", "工作对象", "Workspace"],
      ["session-workspace-switch", "切换并清空未提交草稿", "Switch and discard drafts"],
      ["session-login", "企业登录", "Sign in"],
      ["session-refresh", "重试", "Retry"],
      ["session-logout", "退出", "Sign out"],
      ["session-actor-summary", "切换演示角色", "Switch demo role"],
      ["session-actor-label", "操作身份", "Acting role"],
      ["session-actor-switch", "使用此角色", "Use this role"],
      ["session-actor-note", "本地角色模拟；权限由服务端检查。切换后清空未提交内容。", "Local role simulation with server-enforced permissions. Switching discards unsent drafts."],
    ]) {
      if (node(id)) node(id).textContent = text(zh, en);
    }
    node("session-status").textContent = !session
      ? text("登录状态暂不可用", "Session status unavailable")
      : localRoles(session)
        ? session.principal ? actorName(session.principal) : text("请选择操作身份", "Choose an acting role")
        : session.mode === "local"
        ? text("本地工作区", "Local workspace")
        : locked ? text("请登录企业账号", "Sign in with your enterprise account")
          : actorName(session.principal);
    node("session-description").textContent = notice || (localRoles(session)
      ? text("角色模拟", "Role simulation") + (session.principal ? " · " + roleNames(session.principal.roles) : "")
      : locked
      ? text("登录后才能读取工作内容或签署变更。", "Sign in to read work or approve changes.")
      : session && session.principal ? roleNames(session.principal.roles) : "");
    const actorPicker = node("session-actor-picker"), actorSelector = node("session-actor");
    if (actorPicker && actorSelector) {
      actorPicker.hidden = !localRoles(session);
      if (localRoles(session)) {
        const selected = actorSelector.value;
        actorSelector.replaceChildren();
        for (const actor of session.actors) {
          const option = document.createElement("option");
          option.value = actor.actor_id; option.textContent = actorName(actor);
          actorSelector.append(option);
        }
        actorSelector.value = session.actors.some(actor => actor.actor_id === selected) ? selected
          : session.principal?.actor_id || session.actors[0]?.actor_id || "";
        if (!session.authenticated) actorPicker.open = true;
        node("session-actor-switch").disabled = switchingActor || !actorSelector.value;
      }
    }
    const login = node("session-login");
    login.hidden = !locked || !session.login_url;
    if (!login.hidden) {
      login.href = session.login_url;
      login.target = resumeInNewTab ? "_blank" : "_self";
      login.rel = "noopener";
    }
    node("session-logout").hidden = !session || !session.authenticated || !session.logout_url;
    node("session-refresh").hidden = Boolean(session && (!locked || localRoles(session) && session.csrf_token));
  }

  function validateSession(value) {
    if (!value || !["oidc", "bearer", "local"].includes(value.mode)
      || typeof value.authenticated !== "boolean"
      || typeof value.authentication_required !== "boolean"
      || value.authenticated && (!value.principal || !value.principal.actor_id || !value.principal.tenant_id)) {
      throw new Error("SESSION_RESPONSE_INVALID");
    }
    for (const key of ["login_url", "logout_url", "switch_actor_url"]) {
      if (value[key] != null && !String(value[key]).startsWith("/api/session/")) {
        throw new Error("SESSION_ENDPOINT_INVALID");
      }
    }
    if (localRoles(value) && (value.mode !== "local" || value.authentication_required !== true
      || !Array.isArray(value.actors) || !value.actors.length
      || value.actors.some(actor => !actor || typeof actor.actor_id !== "string" || !actor.actor_id
        || !Array.isArray(actor.roles))
      || new Set(value.actors.map(actor => actor.actor_id)).size !== value.actors.length
      || value.switch_actor_url !== "/api/session/local-actor"
      || value.authenticated && !value.csrf_token
      || value.csrf_token != null && (typeof value.csrf_token !== "string" || !value.csrf_token))) {
      throw new Error("SESSION_RESPONSE_INVALID");
    }
    return value;
  }

  async function refreshSession() {
    if (switchingActor) throw contextChanged();
    if (sessionRequest) return sessionRequest;
    const generation = contextGeneration;
    const pending = (async () => {
      try {
        const response = await transport("/api/session", { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error("SESSION_UNAVAILABLE");
        const next = validateSession(await response.json());
        requireContext(generation);
        setSession(next);
        notice = "";
        emit();
        return session;
      } catch (error) {
        requireContext(generation);
        setSession(null);
        notice = text("无法确认登录状态，请重试。未发送业务操作。", "Session could not be checked. Retry; no work command was sent.");
        emit();
        throw error;
      } finally { if (sessionRequest === pending) sessionRequest = null; }
    })();
    sessionRequest = pending;
    return pending;
  }

  async function refreshWorkspaces() {
    if (workspaceRequest) return workspaceRequest;
    const pending = (async () => {
      const response = await request("/api/workspaces");
      if (!response.ok) throw new Error("WORKSPACE_CATALOG_UNAVAILABLE");
      const value = await readBody(response);
      if (!Array.isArray(value.items) || !value.items.length
        || value.items.some(item => typeof item.workspace_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(item.workspace_id) || typeof item.label !== "string")
        || new Set(value.items.map(item => item.workspace_id)).size !== value.items.length
        || !value.items.some(item => item.workspace_id === value.default_workspace_id)) throw new Error("WORKSPACE_CATALOG_INVALID");
      const saved = window.sessionStorage?.getItem("orgrebase.workspace");
      const selectedId = value.items.some(item => item.workspace_id === saved) ? saved : value.default_workspace_id;
      if (workspaceId !== null && workspaceId !== selectedId) ++contextGeneration;
      workspaceId = selectedId;
      workspaces = value;
      const selector = node("session-workspace");
      if (selector) {
        selector.replaceChildren();
        for (const item of value.items) { const option = document.createElement("option"); option.value = item.workspace_id; option.textContent = item.label; selector.append(option); }
        selector.value = workspaceId;
        node("session-workspace-switch").hidden = value.items.length < 2;
        node("session-workspace-picker").hidden = false;
      }
      return value;
    })().finally(() => { if (workspaceRequest === pending) workspaceRequest = null; });
    workspaceRequest = pending;
    return pending;
  }

  function switchWorkspace() {
    const id = node("session-workspace")?.value;
    if (!workspaces?.items.some(item => item.workspace_id === id) || id === workspaceId) return;
    window.sessionStorage.setItem("orgrebase.workspace", id);
    ++contextGeneration;
    window.location.reload();
  }

  async function request(path, options = {}) {
    const target = new URL(path, window.location.origin);
    const method = (options.method || "GET").toUpperCase();
    const publicProbe = method === "GET" && ["/api/health", "/readyz"].includes(target.pathname);
    if (target.origin !== window.location.origin || !target.pathname.startsWith("/api/") && !publicProbe) {
      throw new Error("WORKSPACE_REQUEST_ORIGIN_DENIED");
    }
    if (publicProbe) {
      const response = await transport(target.pathname + target.search, {
        method, headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store",
        signal: options.signal,
      });
      responseGenerations.set(response, null);
      return response;
    }
    if (switchingActor && !(localRoles(session) && target.pathname === session.switch_actor_url && method === "POST")) {
      throw contextChanged();
    }
    if (!session) await refreshSession();
    const generation = contextGeneration;
    const identitySwitch = localRoles(session) && target.pathname === session.switch_actor_url && method === "POST";
    if (session.authentication_required && !session.authenticated && !identitySwitch) {
      const error = new Error(text("登录已失效，请重新登录。操作未发送。", "Sign in again. The operation was not sent."));
      error.code = "AUTH_SESSION_REQUIRED";
      error.status = 401;
      throw error;
    }
    const scoped = target.pathname.startsWith("/api/workspace/");
    if (scoped && !workspaces) await refreshWorkspaces();
    requireContext(generation);
    const headers = new Headers(options.headers || {});
    headers.delete("X-OrgRebase-Workspace");
    headers.delete("X-OrgRebase-Local-Session");
    if (scoped) headers.set("X-OrgRebase-Workspace", workspaceId);
    if (session.mode !== "local" || localRoles(session)) headers.delete("X-OrgRebase-Actor");
    if ((session.mode === "oidc" || localRoles(session)) && !safeMethods.has(method)) {
      headers.delete("X-CSRF-Token");
      if (identitySwitch && !session.authenticated && !session.csrf_token) {
        headers.set("X-OrgRebase-Local-Session", "initialize");
      } else {
        if (!session.csrf_token) throw new Error("AUTH_CSRF_TOKEN_UNAVAILABLE");
        headers.set("X-CSRF-Token", session.csrf_token);
      }
    }
    const response = await transport(target.pathname + target.search, { ...options, method, headers, credentials: "same-origin", cache: "no-store" });
    requireContext(generation);
    if (scoped && response.ok && response.headers.get("Content-Type")?.includes("application/json")) {
      await window.OrgRebaseWire.verifyResponse(response);
    }
    requireContext(generation);
    if (response.status === 401) {
      setSession({ ...session, authenticated: false, principal: null, csrf_token: null });
      resumeInNewTab = true;
      notice = localRoles(session)
        ? text("角色会话已过期，请重新选择身份。业务操作不会自动重发。", "Role session expired. Select your role again. Work commands are not replayed.")
        : text("会话已过期或被撤销。请在新窗口登录后返回此页刷新；草稿只保留在当前页面，系统不会自动重发。", "Your session expired or was revoked. Sign in in a new window, then return here and refresh. This page retains your draft; no automatic replay occurs.");
      emit();
      window.dispatchEvent(new CustomEvent("orgrebase:sessionended", { detail: { reason: "expired" } }));
    }
    responseGenerations.set(response, contextGeneration);
    return response;
  }

  async function readBody(response, kind = "json") {
    const generation = responseGenerations.get(response);
    if (generation !== null) requireContext(generation);
    try {
      if (kind === "json") return await response.json();
      if (kind === "blob") return await response.blob();
      throw new Error("WORKSPACE_RESPONSE_BODY_TYPE_INVALID");
    } finally { if (generation !== null) requireContext(generation); }
  }

  async function json(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    const response = await request(path, { ...options, headers, body: options.body === undefined ? undefined : JSON.stringify(options.body) });
    let payload = null;
    try { payload = await readBody(response); }
    catch (error) { if (error.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") throw error; }
    if (!response.ok) {
      const code = payload && payload.detail && payload.detail.code || `HTTP_${response.status}`;
      const error = new Error(code);
      error.code = code;
      error.status = response.status;
      error.detail = payload && payload.detail;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  async function logout() {
    if (!session || !session.logout_url) return;
    try {
      setSession(validateSession(await json(session.logout_url, { method: "POST", body: {} })));
      resumeInNewTab = false;
      notice = text("已退出登录。", "Signed out.");
      emit();
      window.dispatchEvent(new CustomEvent("orgrebase:sessionended", { detail: { reason: "logout" } }));
    } catch (_) {
      notice = text("退出请求未确认，请重试。", "Sign-out was not confirmed. Retry.");
      renderSession();
    }
  }

  async function switchActor() {
    const actorId = node("session-actor")?.value;
    if (switchingActor || !localRoles(session) || !session.actors.some(actor => actor.actor_id === actorId)) return;
    switchingActor = true;
    // Invalidate the old role before dispatching its replacement. Its pending
    // responses may arrive while the server is rotating the cookie.
    ++contextGeneration;
    renderSession();
    try {
      const next = validateSession(await json(session.switch_actor_url, { method: "POST", body: { actor_id: actorId } }));
      setSession(next);
      notice = "";
      node("session-actor-picker").open = false;
      switchingActor = false;
      window.dispatchEvent(new CustomEvent("orgrebase:sessionended", { detail: { reason: "role-switch" } }));
      emit();
      window.dispatchEvent(new CustomEvent("orgrebase:workspacechange", { detail: { source: "role-switch" } }));
    } catch (error) {
      // The cookie may already have rotated even when the response was lost.
      // Retire all old-role views and reconcile with a read, never a replay.
      setSession(null);
      sessionRequest = null;
      window.dispatchEvent(new CustomEvent("orgrebase:sessionended", { detail: { reason: "role-switch" } }));
      emit();
      switchingActor = false;
      try {
        await refreshSession();
        notice = text("已重新核对当前身份；原切换请求未重发。", "Current identity rechecked; the original switch was not replayed.");
        window.dispatchEvent(new CustomEvent("orgrebase:workspacechange", { detail: { source: "role-switch" } }));
      } catch (_) {
        notice = text("无法确认当前身份，请刷新登录状态后继续。未提交内容已清空，操作不会自动重发。", "Current identity is unconfirmed. Retry the session check to continue. Unsent content was cleared; commands are not replayed.");
      }
    } finally {
      switchingActor = false;
      renderSession();
    }
  }

  window.OrgRebaseClient = Object.freeze({ fetch: request, json, readBody, refreshSession, refreshWorkspaces, logout, session: () => session, workspace: () => workspaceId });
  node("session-workspace-switch")?.addEventListener("click", switchWorkspace);
  node("session-logout")?.addEventListener("click", logout);
  node("session-refresh")?.addEventListener("click", () => refreshSession().then(() => window.dispatchEvent(new CustomEvent("orgrebase:workspacechange"))).catch(() => {}));
  node("session-actor-switch")?.addEventListener("click", switchActor);
  window.addEventListener("orgrebase:languagechange", renderSession);
  renderSession();
  refreshSession().catch(() => {});
})();
