/* Shenlun Review Web - frontend logic */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var resultCard = $("result-card");
  var resultEl = $("result");
  var statusEl = $("result-status");
  var btnCopy = $("btn-copy");
  var btnNew = $("btn-new");
  var btnRefill = $("btn-refill");
  var btnSubmit = null; // 当前面板的提交按钮，提交时取

  /* ---- settings (localStorage) ---- */
  var CFG_KEY = "shenlun-web-cfg";
  var cfg = { baseUrl: "", key: "", model: "glm-4.6" };
  try {
    cfg = Object.assign(cfg, JSON.parse(localStorage.getItem(CFG_KEY) || "{}"));
  } catch (e) { /* ignore */ }

  function saveCfg() {
    try { localStorage.setItem(CFG_KEY, JSON.stringify(cfg)); } catch (e) { /* ignore */ }
  }

  /* ---- server capability probe ---- */
  var serverDefaultReady = false;
  fetch("/api/health").then(function (r) { return r.json(); }).then(function (h) {
    serverDefaultReady = !!h.default_ready;
    if (serverDefaultReady) {
      var hint = document.querySelector(".dialog .hint");
      if (hint) {
        hint.textContent = "服务器已内置默认模型，无需任何配置即可使用。"
          + "如想用自己的接口，可在下方填写（OpenAI 兼容格式），Key 只保存在本机浏览器。";
      }
    }
  }).catch(function () { /* ignore */ });

  /* ---- settings dialog ---- */
  var mask = $("settings-mask");

  function openSettings() {
    $("cfg-base").value = cfg.baseUrl;
    $("cfg-key").value = cfg.key;
    $("cfg-model").value = cfg.model;
    $("settings-username").textContent = me ? me.username : "未登录";
    mask.classList.remove("hidden");
  }

  function closeSettings() {
    mask.classList.add("hidden");
  }

  $("btn-settings").addEventListener("click", openSettings);
  $("btn-settings-close").addEventListener("click", closeSettings);
  $("cfg-cancel").addEventListener("click", closeSettings);
  $("cfg-save").addEventListener("click", function () {
    cfg.baseUrl = $("cfg-base").value.trim().replace(/\/+$/, "");
    cfg.key = $("cfg-key").value.trim();
    cfg.model = $("cfg-model").value.trim();
    saveCfg();
    closeSettings();
  });
  mask.addEventListener("click", function (e) {
    if (e.target === mask) closeSettings();
  });

  /* ---- mode panels ---- */
  var modeCards = document.querySelectorAll(".mode-card");
  var panels = {
    analyze: $("panel-analyze"),
    grade: $("panel-grade"),
    grab: $("panel-grab"),
  };

  modeCards.forEach(function (card) {
    card.addEventListener("click", function () {
      modeCards.forEach(function (c) { c.classList.remove("active"); });
      card.classList.add("active");
      var name = card.dataset.panel;
      Object.keys(panels).forEach(function (k) {
        panels[k].classList.toggle("hidden", k !== name);
      });
      resultCard.classList.add("hidden");
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  });

  /* ---- grab panel: samples + url fetch ---- */
  function switchToPanel(name) {
    modeCards.forEach(function (c) {
      c.classList.toggle("active", c.dataset.panel === name);
    });
    Object.keys(panels).forEach(function (k) {
      panels[k].classList.toggle("hidden", k !== name);
    });
    resultCard.classList.add("hidden");
  }

  /* grab panel state */
  var lastMaterials = "";
  var lastQuestions = [];

  function fillPreview(materials, questions, reference) {
    lastMaterials = materials;
    lastQuestions = questions || [];
    $("pv-material").value = materials;
    $("pv-reference").value = reference || "";

    var qList = $("pv-question-list");
    qList.innerHTML = "";
    if (lastQuestions.length > 1) {
      qList.classList.remove("hidden");
      lastQuestions.forEach(function (q, i) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "sample-item";
        btn.innerHTML = "<span class='sample-name'>第" + (i + 1) + "题</span>"
          + "<span class='sample-meta'>" + q.stem.replace(/</g, "&lt;").replace(/\n/g, " ") + "</span>";
        btn.addEventListener("click", function () {
          qList.querySelectorAll(".sample-item").forEach(function (b) {
            b.classList.remove("active");
          });
          btn.classList.add("active");
          $("pv-question").value = q.stem;
        });
        qList.appendChild(btn);
        if (i === 0) {
          btn.classList.add("active");
          $("pv-question").value = q.stem;
        }
      });
    } else {
      qList.classList.add("hidden");
      $("pv-question").value = lastQuestions.length ? lastQuestions[0].stem : "";
    }

    $("grab-preview").classList.remove("hidden");
    $("grab-preview").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /* ---- 内置真题库列表 ---- */
  var grabStatus = $("grab-status");
  var papersCache = null;

  function renderPapers(items, filter) {
    var list = $("papers-list");
    list.innerHTML = "";
    var kw = (filter || "").trim().toLowerCase();
    var shown = 0;
    items.forEach(function (p) {
      if (kw && p.title.toLowerCase().indexOf(kw) < 0) return;
      shown++;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "paper-item";
      btn.textContent = p.title;
      btn.addEventListener("click", function () { grabPaper(p.id, btn); });
      list.appendChild(btn);
    });
    if (!shown) {
      list.innerHTML = "<p class='hint'>没有匹配的试卷，换个关键词试试</p>";
    }
  }

  function grabPaper(paperId, btn) {
    var hasOverride = !!(cfg.baseUrl && cfg.key && cfg.model);
    if (!serverDefaultReady && !hasOverride) {
      openSettings();
      alert("请先填写 API 设置");
      return;
    }
    $("papers-list").querySelectorAll(".paper-item").forEach(function (b) {
      b.classList.remove("active");
    });
    btn.classList.add("active");
    grabStatus.textContent = "正在抓取整卷并提取题目，约需 10–30 秒…";
    fetch("/api/grab-paper", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        paperId: paperId,
        api: hasOverride ? { baseUrl: cfg.baseUrl, key: cfg.key, model: cfg.model } : null,
      }),
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.error) {
        grabStatus.textContent = "出错：" + data.error;
        return;
      }
      if (!data.materials && (!data.questions || !data.questions.length)) {
        grabStatus.textContent = "这份卷子未能提取到题目，换一份试试";
        return;
      }
      grabStatus.textContent = "提取成功，请在下方预览确认";
      fillPreview(data.materials, data.questions, data.reference);
    }).catch(function (err) {
      grabStatus.textContent = "出错：" + err.message;
    });
  }

  fetch("/api/papers").then(function (r) { return r.json(); }).then(function (data) {
    if (data.error) {
      grabStatus.textContent = "出错：" + data.error;
      return;
    }
    papersCache = data.papers || [];
    grabStatus.textContent = "共 " + papersCache.length + " 套试卷，点击任意一份开始";
    renderPapers(papersCache, $("paper-filter").value);
  }).catch(function (err) {
    grabStatus.textContent = "获取真题列表失败：" + err.message;
  });

  $("paper-filter").addEventListener("input", function () {
    if (papersCache) renderPapers(papersCache, this.value);
  });

  $("btn-grab-analyze").addEventListener("click", function () {
    var q = $("pv-question").value.trim();
    var m = $("pv-material").value.trim();
    if (!q && !m) {
      alert("题干和材料都是空的");
      return;
    }
    switchToPanel("analyze");
    $("q-analyze").value = $("pv-question").value;
    $("m-analyze").value = $("pv-material").value;
    var ref = $("pv-reference").value;
    var refField = panels.analyze.querySelector('[data-field="reference"]');
    if (refField) refField.value = ref;
    handleSubmit(panels.analyze);
  });

  /* ---- waiting animation ---- */
  var waitTimer = null;
  var firstDelta = false;
  var loadingBar = $("loading-bar");

  function startWaitingUi() {
    firstDelta = false;
    loadingBar.classList.remove("hidden");
    var started = Date.now();
    var showWait = function () {
      var sec = Math.floor((Date.now() - started) / 1000);
      statusEl.innerHTML = "正在思考<span class=\"thinking-dots\"><span></span><span></span><span></span></span>"
        + " 已等待 " + sec + " 秒 · 申论批改通常需要 20–60 秒，请稍候";
    };
    showWait();
    waitTimer = setInterval(showWait, 1000);
  }

  function stopWaitingUi(silent) {
    if (waitTimer) { clearInterval(waitTimer); waitTimer = null; }
    loadingBar.classList.add("hidden");
    if (!silent) {
      statusEl.textContent = "生成中…";
      statusEl.style.color = "";
    }
  }

  /* ---- submit + streaming render ---- */
  [panels.analyze, panels.grade].forEach(function (panel) {
    panel.addEventListener("submit", function (e) {
      e.preventDefault();
      handleSubmit(panel);
    });
  });

  function handleSubmit(panel) {
    var mode = panel.dataset.mode;
    var get = function (name) {
      var el = panel.querySelector('[data-field="' + name + '"]');
      return el ? el.value.trim() : "";
    };
    var question = get("question");
    var material = get("material");
    var answer = get("answer");
    var reference = get("reference");
    btnSubmit = panel.querySelector('button[type="submit"]');

    if (!question && !material) {
      alert("请填写题干和给定资料");
      return;
    }
    if (mode === "grade" && !answer) {
      alert("批改模式需要填写「我的作答」");
      return;
    }

    var hasOverride = !!(cfg.baseUrl && cfg.key && cfg.model);
    if (!serverDefaultReady && !hasOverride) {
      openSettings();
      alert("请先填写 API 设置");
      return;
    }

    startStream({
      mode: mode,
      question: question,
      material: material,
      reference: reference,
      answer: answer,
      api: hasOverride ? { baseUrl: cfg.baseUrl, key: cfg.key, model: cfg.model } : null,
    });
  }

  function render(text) {
    if (window.marked) {
      resultEl.innerHTML = window.marked.parse(text, { breaks: true });
    } else {
      resultEl.textContent = text;
      resultEl.style.whiteSpace = "pre-wrap";
    }
  }

  var raw = "";
  var renderTimer = null;

  function startStream(body) {
    raw = "";
    resultEl.innerHTML = "";
    resultCard.classList.remove("hidden");
    btnSubmit.disabled = true;
    btnCopy.classList.add("hidden");
    btnRefill.classList.add("hidden");
    startWaitingUi();
    resultCard.scrollIntoView({ behavior: "smooth", block: "start" });

    fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (resp) {
      if (!resp.ok || !resp.body) {
        return resp.json().then(function (err) {
          throw new Error(err.error || ("HTTP " + resp.status));
        });
      }
      var reader = resp.body.getReader();
      var decoder = new TextDecoder("utf-8");
      var buf = "";

      function pump() {
        return reader.read().then(function (r) {
          if (r.done) { finish(); return; }
          buf += decoder.decode(r.value, { stream: true });
          var idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            var frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            handleFrame(frame);
          }
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      stopWaitingUi(true);
      statusEl.textContent = "出错：" + err.message;
      statusEl.style.color = "var(--danger)";
      btnSubmit.disabled = false;
    });
  }

  function handleFrame(frame) {
    var data = null;
    frame.split("\n").forEach(function (line) {
      if (line.indexOf("data:") === 0) {
        try { data = JSON.parse(line.slice(5).trim()); } catch (e) { /* ignore */ }
      }
    });
    if (!data) return;
    if (data.t) {
      if (!firstDelta) {
        firstDelta = true;
        stopWaitingUi(false);
      }
      raw += data.t;
      if (!renderTimer) {
        renderTimer = setTimeout(function () {
          renderTimer = null;
          render(raw);
        }, 120);
      }
    }
    if (data.e) {
      stopWaitingUi(true);
      statusEl.textContent = "出错：" + data.e;
      statusEl.style.color = "var(--danger)";
    }
    if (data.done) finish();
  }

  function finish() {
    stopWaitingUi(true);
    if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
    render(raw);
    statusEl.textContent = "完成";
    statusEl.style.color = "";
    if (btnSubmit) btnSubmit.disabled = false;
    if (raw) btnCopy.classList.remove("hidden");
  }

  /* ---- copy / new ---- */
  btnCopy.addEventListener("click", function () {
    if (!raw) return;
    var done = function () { statusEl.textContent = "已复制到剪贴板"; };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(raw).then(done);
    } else {
      var ta = document.createElement("textarea");
      ta.value = raw;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      done();
    }
  });

  btnNew.addEventListener("click", function () {
    panels.analyze.reset();
    panels.grade.reset();
    resultCard.classList.add("hidden");
    raw = "";
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  /* ======================================================================
     账号与历史会话 / 学生画像
     ------------------------------------------------------------------
     登录态由服务端 HttpOnly Cookie 维护；历史与学生画像按账号隔离，
     全部走真实接口。画像由服务端在学练/批改后自动提炼（见 server.py）。
     ====================================================================== */
  var PREVIEW = false;

  /* ---- 账号：登录门 / 登录 / 注册 / 退出 ---- */
  var userGreeting = $("user-greeting");
  var GREETINGS = ["你好", "加油", "今天也要坚持练", "滴水穿石", "稳住，我们能赢"];
  var authMask = $("auth-mask");
  var authMode = "login"; // login | register
  var me = null;          // {username} 已登录用户

  function authError(msg) {
    var el = $("auth-error");
    el.textContent = msg;
    el.classList.remove("hidden");
  }

  function showAuth(mode, message) {
    authMode = mode || "login";
    $("tab-login").classList.toggle("active", authMode === "login");
    $("tab-register").classList.toggle("active", authMode === "register");
    $("auth-register-extra").classList.toggle("hidden", authMode !== "register");
    $("btn-auth-go").textContent = authMode === "login" ? "登录" : "注册并登录";
    if (message) authError(message);
    else $("auth-error").classList.add("hidden");
    authMask.classList.remove("hidden");
  }

  function afterLogin(name) {
    me = { username: name };
    userGreeting.textContent = GREETINGS[Math.floor(Math.random() * GREETINGS.length)]
      + "，" + name + "！";
    userGreeting.classList.remove("hidden");
    // 历史入口脉冲引导；点开一次后停止
    var histBtn = $("btn-history");
    histBtn.classList.add("attention");
    histBtn.classList.remove("hidden");
    authMask.classList.add("hidden");
  }

  function setAuthBusy(busy) {
    var go = $("btn-auth-go");
    go.disabled = busy;
    go.textContent = (authMode === "login" ? "登录" : "注册并登录") + (busy ? "…" : "");
  }

  function authSubmit() {
    var username = $("auth-user").value.trim();
    var password = $("auth-pass").value;
    if (username.length < 2) { authError("请输入用户名（2–20 位）"); return; }
    if (password.length < 6) { authError("密码至少 6 位"); return; }
    if (authMode === "register" && password !== $("auth-pass2").value) {
      authError("两次输入的密码不一致");
      return;
    }
    var invite = $("auth-invite").value.trim();
    setAuthBusy(true);
    var url = authMode === "login" ? "/api/login" : "/api/register";
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username, password: password, invite: invite })
    }).then(function (r) {
      if (!r.ok) {
        return r.json().then(function (err) {
          throw new Error(err.error || ("HTTP " + r.status));
        });
      }
      return r.json();
    }).then(function () {
      setAuthBusy(false);
      afterLogin(username);
    }).catch(function (err) {
      setAuthBusy(false);
      authError(err.message);
    });
  }

  $("tab-login").addEventListener("click", function () { showAuth("login"); });
  $("tab-register").addEventListener("click", function () { showAuth("register"); });
  $("btn-auth-go").addEventListener("click", authSubmit);
  $("auth-pass2").addEventListener("keydown", function (e) {
    if (e.key === "Enter") authSubmit();
  });

  /* ---- 退出登录 / 注销账号（入口在设置弹层） ---- */
  function doLocalLogout() {
    me = null;
    userGreeting.classList.add("hidden");
    $("btn-history").classList.add("hidden");
    closeSettings();
    showAuth("login");
  }

  $("btn-logout").addEventListener("click", function () {
    if (!confirm("退出登录？历史记录仍保存在服务器，下次登录还能看到。")) return;
    fetch("/api/logout", { method: "POST" }).then(doLocalLogout);
  });

  $("btn-account-delete").addEventListener("click", function () {
    if (!confirm("确定注销账号？全部历史记录与学习画像将被删除。")) return;
    if (!confirm("再次确认：此操作不可恢复，真的要注销吗？")) return;
    fetch("/api/account", { method: "DELETE" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      doLocalLogout();
    }).catch(function (err) {
      alert("注销失败：" + err.message);
    });
  });

  /* ---- 历史会话 ---- */
  var historyMask = $("history-mask");
  var convsCache = [];
  var viewedConv = null; // 当前正在回看的会话（“填回表单”用）

  function fetchConvs(q) {
    fetch("/api/conversations?q=" + encodeURIComponent(q || "")).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (data) {
      convsCache = data.conversations || [];
      renderConvs(convsCache);
    }).catch(function (err) {
      $("history-status").textContent = "获取历史失败：" + err.message;
    });
  }

  // 预览模式本地过滤；真实接口的 q 由服务端过滤
  function renderConvs(items) {
    var list = $("history-list");
    list.innerHTML = "";
    if (!items.length) {
      var empty = document.createElement("div");
      empty.className = "history-empty";
      empty.innerHTML = "还没有历史记录<br>完成一次解析/批改或学习练习后，这里会自动出现";
      list.appendChild(empty);
    }
    items.forEach(function (conv) {
      var item = document.createElement("div");
      item.className = "conv-item";
      var badge = conv.kind === "learn" ? "🎓 学习" :
        (conv.mode === "grade" ? "✅ 批改" : "📖 解析");
      item.innerHTML = "<div class='conv-title'>" + esc(conv.title) + "</div>"
        + "<div class='conv-meta'>"
        + "<span class='conv-badge'>" + badge + "</span>"
        + "<span class='conv-time'>" + relTime((conv.updated_at || conv.created_at) * 1000) + "</span>"
        + "</div>"
        + "<button type='button' class='conv-del' aria-label='删除'>🗑</button>";
      item.addEventListener("click", function () { viewConv(conv.id); });
      item.querySelector(".conv-del").addEventListener("click", function (e) {
        e.stopPropagation();
        if (!confirm("删除这条历史记录？删除后无法恢复。")) return;
        fetch("/api/conversations/" + conv.id, { method: "DELETE" }).then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          convsCache = convsCache.filter(function (c) { return c.id !== conv.id; });
          renderConvs(convsCache);
        }).catch(function (err) {
          alert("删除失败：" + err.message);
        });
      });
      list.appendChild(item);
    });
    $("history-status").textContent = items.length
      ? items.length + " 条记录 · 仅显示当前账号"
      : "仅显示当前登录账号的记录";
  }

  function openHistory() {
    $("btn-history").classList.remove("attention");
    historyMask.classList.remove("hidden");
    $("history-filter").value = "";
    fetchConvs("");
  }

  function closeHistory() {
    historyMask.classList.add("hidden");
  }

  $("btn-history").addEventListener("click", openHistory);
  $("btn-history-close").addEventListener("click", closeHistory);
  historyMask.addEventListener("click", function (e) {
    if (e.target === historyMask) closeHistory();
  });
  $("history-filter").addEventListener("input", function () {
    fetchConvs(this.value);
  });

  function viewConv(id) {
    // 列表条目只有标题/时间，正文与消息需要按详情接口取
    fetch("/api/conversations/" + id).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (data) {
      var conv = data.conversation;
      if (!conv) return;
      if (conv.kind === "learn") openLearnFromHistory(conv);
      else viewReview(conv);
    }).catch(function (err) {
      alert("加载历史记录失败：" + err.message);
    });
  }

  function viewReview(conv) {
    viewedConv = conv;
    $("learn-section").classList.add("hidden");
    resultCard.classList.remove("hidden");
    statusEl.textContent = "历史回看 · 只读";
    render(conv.output || "（该记录没有保存到正文）");
    btnCopy.classList.remove("hidden");
    btnRefill.classList.remove("hidden");
    closeHistory();
    resultCard.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function openLearnFromHistory(conv) {
    viewedConv = conv;
    resultCard.classList.add("hidden");
    learnState = {
      question: conv.question || conv.title,
      material: conv.material || "",
      reference: conv.reference || "",
      history: (conv.messages || []).slice(),
      conversationId: conv.id,
    };
    $("chat-log").innerHTML = "";
    (conv.messages || []).forEach(function (m) { chatBubble(m.role, m.content); });
    $("learn-section").classList.remove("hidden");
    closeHistory();
    learnSectionScroll();
  }

  /* ---- 填回表单（解析/批改记录回看后可修改重跑） ---- */
  btnRefill.addEventListener("click", function () {
    var conv = viewedConv;
    if (!conv) return;
    var target = conv.mode === "grade" ? panels.grade : panels.analyze;
    switchToPanel(conv.mode === "grade" ? "grade" : "analyze");
    target.querySelector('[data-field="question"]').value = conv.question || "";
    target.querySelector('[data-field="material"]').value = conv.material || "";
    var refField = target.querySelector('[data-field="reference"]');
    if (refField) refField.value = conv.reference || "";
    if (conv.mode === "grade") {
      target.querySelector('[data-field="answer"]').value = conv.answer || "";
    }
    resultCard.classList.add("hidden");
    btnRefill.classList.add("hidden");
    viewedConv = null;
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  /* ---- 工具：相对时间 / HTML 转义 ---- */
  function relTime(ts) {
    var diff = Date.now() - ts;
    if (diff < 60e3) return "刚刚";
    if (diff < 3.6e6) return Math.floor(diff / 60e3) + " 分钟前";
    if (diff < 8.64e7) return Math.floor(diff / 3.6e6) + " 小时前";
    if (diff < 6.048e8) return Math.floor(diff / 8.64e7) + " 天前";
    var d = new Date(ts);
    var sameYear = d.getFullYear() === new Date().getFullYear();
    var s = (d.getMonth() + 1) + "月" + d.getDate() + "日";
    return sameYear ? s : d.getFullYear() + "年" + s;
  }

  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  /* ---- 启动：检查登录态 ---- */
  function checkAuth() {
    fetch("/api/me").then(function (r) {
      if (!r.ok) throw new Error("unauthorized");
      return r.json();
    }).then(function (data) {
      afterLogin(data.username);
    }).catch(function () {
      showAuth("login");
    });
  }
  checkAuth();

  /* ---- 学生画像面板 ---- */
  var profileMask = $("profile-mask");
  var MEMO_LABELS = {
    background: "🧭 备考身份",
    pitfall: "⚠️ 易踩的坑",
    progress: "📈 学习进度",
    strength: "💪 做得好",
    preference: "🎯 教学偏好",
  };

  function renderMemories(items) {
    var list = $("profile-list");
    list.innerHTML = "";
    if (!items.length) {
      var empty = document.createElement("div");
      empty.className = "history-empty";
      empty.innerHTML = "画像还没建立<br>完成几轮学习或一次批改后，老师会自动总结你的画像";
      list.appendChild(empty);
      return;
    }
    items.forEach(function (m) {
      var item = document.createElement("div");
      item.className = "memory-item";
      item.innerHTML = "<div class='conv-title'>" + esc(m.content) + "</div>"
        + "<div class='conv-meta'>"
        + "<span class='conv-badge'>" + (MEMO_LABELS[m.kind] || "备注") + " ×" + Math.round(m.weight) + "</span>"
        + "<span class='conv-time'>" + relTime(m.updated_at * 1000) + "</span>"
        + "</div>"
        + "<button type='button' class='conv-del' aria-label='删除'>🗑</button>";
      item.querySelector(".conv-del").addEventListener("click", function (e) {
        e.stopPropagation();
        if (!confirm("删除这条画像条目？删除后老师不会再参考它。")) return;
        fetch("/api/profile/" + m.id, { method: "DELETE" }).then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          item.remove();
        }).catch(function (err) {
          alert("删除失败：" + err.message);
        });
      });
      list.appendChild(item);
    });
    var status = $("profile-status");
    if (status) status.textContent = items.length + " 条画像";
  }

  function openProfile() {
    profileMask.classList.remove("hidden");
    fetch("/api/profile").then(function (r) { return r.json(); }).then(function (data) {
      renderMemories(data.memories || []);
      renderRecent(data.recent || []);
    }).catch(function () {
      renderMemories([]);
      renderRecent([]);
    });
  }

  function renderRecent(items) {
    var list = $("recent-list");
    list.innerHTML = "";
    if (!items.length) {
      var empty = document.createElement("div");
      empty.className = "history-empty";
      empty.textContent = "还没有近期练习记录";
      list.appendChild(empty);
      return;
    }
    items.forEach(function (n) {
      var item = document.createElement("div");
      item.className = "memory-item";
      item.innerHTML = "<div class='conv-title'>" + esc(n.summary) + "</div>"
        + "<div class='conv-meta'>"
        + "<span class='conv-time'>" + relTime(n.created_at * 1000) + "</span>"
        + "</div>";
      list.appendChild(item);
    });
  }

  $("btn-profile").addEventListener("click", openProfile);
  $("btn-profile-close").addEventListener("click", function () {
    profileMask.classList.add("hidden");
  });
  profileMask.addEventListener("click", function (e) {
    if (e.target === profileMask) profileMask.classList.add("hidden");
  });

  /* ---- 学习模式（多轮对话） ---- */
  var learnState = null; // {question, material, reference, history: []}
  var learnBusy = false;

  function chatBubble(role, text) {
    var div = document.createElement("div");
    div.className = "bubble " + (role === "user" ? "bubble-user" : "bubble-teacher");
    if (text) {
      if (window.marked) div.innerHTML = window.marked.parse(text, { breaks: true });
      else div.textContent = text;
    }
    $("chat-log").appendChild(div);
    scrollChatLog();
    return div;
  }

  function renderBubble(div, text) {
    if (window.marked) div.innerHTML = window.marked.parse(text, { breaks: true });
    else { div.textContent = text; div.style.whiteSpace = "pre-wrap"; }
  }

  function scrollChatLog() {
    var log = $("chat-log");
    log.scrollTop = log.scrollHeight;
  }

  function learnSectionScroll() {
    $("learn-section").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function startLearn(question, material, reference) {
    if (!question && !material) {
      alert("请先填写题干和给定资料");
      return;
    }
    learnState = { question: question, material: material, reference: reference, history: [] };
    $("chat-log").innerHTML = "";
    $("learn-section").classList.remove("hidden");
    resultCard.classList.add("hidden");
    learnSectionScroll();
    learnTurn(); // 开场：老师先开口
  }

  function learnTurn(extraUserText) {
    if (learnBusy || !learnState) return;
    learnBusy = true;

    // 先放学生的气泡，再创建老师的气泡，保证 DOM 顺序是问答交错
    if (extraUserText) {
      learnState.history.push({ role: "user", content: extraUserText });
      chatBubble("user", extraUserText);
    }

    var bubble = chatBubble("assistant");
    bubble.innerHTML = "<span class='thinking-dots'><span></span><span></span><span></span></span>";

    var hasOverride = !!(cfg.baseUrl && cfg.key && cfg.model);
    fetch("/api/learn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: learnState.question,
        material: learnState.material,
        reference: learnState.reference,
        history: learnState.history,
        conversationId: learnState.conversationId || null,
        userTurn: extraUserText || null,
        api: hasOverride ? { baseUrl: cfg.baseUrl, key: cfg.key, model: cfg.model } : null,
      }),
    }).then(function (resp) {
      if (!resp.ok || !resp.body) {
        return resp.json().then(function (err) {
          throw new Error(err.error || ("HTTP " + resp.status));
        });
      }
      var reader = resp.body.getReader();
      var decoder = new TextDecoder("utf-8");
      var buf = "";
      var acc = "";

      function pump() {
        return reader.read().then(function (r) {
          if (r.done) { learnFinish(bubble, acc); return; }
          buf += decoder.decode(r.value, { stream: true });
          var idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            var frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            var data = null;
            frame.split("\n").forEach(function (line) {
              if (line.indexOf("data:") === 0) {
                try { data = JSON.parse(line.slice(5).trim()); } catch (e) { /* ignore */ }
              }
            });
            if (!data) continue;
            if (data.c) {
              learnState.conversationId = data.c; // 服务端会话 id，续聊时带回
            }
            if (data.t) {
              acc += data.t;
              renderBubble(bubble, acc);
              scrollChatLog();
            }
            if (data.e) {
              acc += "\n\n**出错：" + data.e + "**";
              renderBubble(bubble, acc);
            }
          }
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      renderBubble(bubble, "**出错：" + err.message + "**");
      learnBusy = false;
    });
  }

  function learnFinish(bubble, acc) {
    renderBubble(bubble, acc || "（本轮没有返回内容，请重试）");
    if (acc) learnState.history.push({ role: "assistant", content: acc });
    learnBusy = false;
    scrollChatLog();
  }

  function sendChat() {
    var input = $("chat-input");
    var text = input.value.trim();
    if (!text || learnBusy) return;
    input.value = "";
    learnTurn(text);
  }

  $("btn-chat-send").addEventListener("click", sendChat);
  $("chat-input").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  $("btn-chat-answer").addEventListener("click", function () {
    learnTurn("请直接给我标准答案，我想对照学习。");
  });
  $("btn-learn-exit").addEventListener("click", function () {
    // 退出前让老师把这几轮沉淀进画像（不等下次节奏）
    if (learnState && learnState.conversationId) {
      fetch("/api/profile/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversationId: learnState.conversationId }),
      }).catch(function () { /* 提炼失败静默 */ });
    }
    $("learn-section").classList.add("hidden");
    learnState = null;
  });

  // 解析面板的学习入口
  $("btn-analyze-learn").addEventListener("click", function () {
    var get = function (name) {
      var el = panels.analyze.querySelector('[data-field="' + name + '"]');
      return el ? el.value.trim() : "";
    };
    startLearn(get("question"), get("material"), get("reference"));
  });

  // 抓题预览的学习入口（保持当前抓题面板，不切换页面）
  $("btn-grab-learn").addEventListener("click", function () {
    startLearn($("pv-question").value, $("pv-material").value, $("pv-reference").value);
  });

  /* ---- 材料全屏查看 ---- */
  var fsTarget = null;
  var fsLastScroll = 0; // 记住上次读到的位置，重新打开时回到那里
  document.querySelectorAll(".expand-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var src = $(btn.dataset.target);
      if (!src) return;
      fsTarget = src;
      var ta = $("fs-textarea");
      ta.value = src.value;
      $("fs-mask").classList.remove("hidden");
      // 不自动聚焦（避免手机弹键盘、视图跳到光标处）；滚动到上次位置或顶部
      ta.blur();
      ta.scrollTop = fsLastScroll;
      ta.setSelectionRange(0, 0);
    });
  });
  function closeFs() {
    fsLastScroll = $("fs-textarea").scrollTop;
    if (fsTarget) fsTarget.value = $("fs-textarea").value;
    $("fs-mask").classList.add("hidden");
    fsTarget = null;
  }
  $("fs-close").addEventListener("click", closeFs);
  $("fs-mask").addEventListener("click", function (e) {
    if (e.target === $("fs-mask")) closeFs();
  });

})();
