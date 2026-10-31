/* Shenlun Review Web - frontend logic */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var resultCard = $("result-card");
  var resultEl = $("result");
  var statusEl = $("result-status");
  var btnCopy = $("btn-copy");
  var btnNew = $("btn-new");
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
    mask.classList.remove("hidden");
  }

  function closeSettings() {
    mask.classList.add("hidden");
  }

  $("btn-settings").addEventListener("click", openSettings);
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
