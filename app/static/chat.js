/* Clubhouse Autopilot - Chat Frontend */

/**
 * ChatUI manages a single chat instance (standalone page or widget).
 * Handles conversation history, SSE streaming, markdown rendering,
 * and document uploads with extraction.
 */
class ChatUI {
  constructor(container, siteId) {
    this.container = container;
    this.siteId = siteId;
    this.api = `/api/sites/${siteId}/chat/message`;
    this.uploadApi = `/api/sites/${siteId}/documents/upload`;
    this.healthApi = `/api/sites/${siteId}/analysis/data-health`;
    this.messages = []; // {role, content}
    this.streaming = false;
    this.pendingFiles = [];

    this.messagesEl = container.querySelector(".chat-messages");
    this.inputEl = container.querySelector(".chat-input");
    this.sendBtn = container.querySelector(".chat-send-btn");
    this.suggestionsEl = container.querySelector(".chat-suggestions");
    this.attachBtn = container.querySelector(".chat-attach-btn");
    this.fileInput = container.querySelector(".chat-file-input");
    this.filePreviewBar = container.querySelector(".file-preview-bar");
    this.trustStripEl = this._ensureTrustStrip();

    this._bind();
    this._loadDataTrust();
  }

  _bind() {
    this.sendBtn.addEventListener("click", () => this.send());
    this.inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this.send();
      }
    });

    // Auto-resize textarea
    this.inputEl.addEventListener("input", () => {
      this.inputEl.style.height = "auto";
      this.inputEl.style.height = Math.min(this.inputEl.scrollHeight, 120) + "px";
    });

    // Suggestion chips
    if (this.suggestionsEl) {
      this.suggestionsEl.querySelectorAll(".suggestion-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          this.inputEl.value = chip.textContent;
          this.send();
        });
      });
    }

    // Attach button
    if (this.attachBtn && this.fileInput) {
      this.attachBtn.addEventListener("click", () => this.fileInput.click());
      this.fileInput.addEventListener("change", () => {
        for (const file of this.fileInput.files) {
          this._addPendingFile(file);
        }
        this.fileInput.value = "";
      });
    }

    // Drag & drop on messages area
    if (this.messagesEl) {
      this.messagesEl.addEventListener("dragover", (e) => {
        e.preventDefault();
        this.messagesEl.classList.add("drag-over");
      });
      this.messagesEl.addEventListener("dragleave", () => {
        this.messagesEl.classList.remove("drag-over");
      });
      this.messagesEl.addEventListener("drop", (e) => {
        e.preventDefault();
        this.messagesEl.classList.remove("drag-over");
        for (const file of e.dataTransfer.files) {
          this._addPendingFile(file);
        }
      });
    }
  }

  _addPendingFile(file) {
    const allowed = ["image/jpeg", "image/png", "application/pdf", "text/csv"];
    if (!allowed.includes(file.type)) {
      alert(`File type not supported: ${file.type}\nAccepted: JPEG, PNG, PDF, CSV`);
      return;
    }
    const maxSize = 10 * 1024 * 1024;
    if (file.size > maxSize) {
      alert(`File too large: ${(file.size / 1024 / 1024).toFixed(1)}MB (max 10MB)`);
      return;
    }
    this.pendingFiles.push(file);
    this._renderFilePreview();
  }

  _renderFilePreview() {
    if (!this.filePreviewBar) return;
    if (this.pendingFiles.length === 0) {
      this.filePreviewBar.innerHTML = "";
      this.filePreviewBar.style.display = "none";
      return;
    }

    this.filePreviewBar.style.display = "flex";
    this.filePreviewBar.innerHTML = this.pendingFiles.map((file, i) => {
      const icon = file.type.startsWith("image/") ? "🖼️" :
                   file.type === "application/pdf" ? "📄" : "📊";
      const size = (file.size / 1024).toFixed(0);
      return `<div class="file-preview-chip">
        <span>${icon} ${this._escapeHtml(file.name)} (${size}KB)</span>
        <button class="file-preview-remove" data-idx="${i}">&times;</button>
      </div>`;
    }).join("");

    this.filePreviewBar.querySelectorAll(".file-preview-remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = parseInt(btn.dataset.idx);
        this.pendingFiles.splice(idx, 1);
        this._renderFilePreview();
      });
    });
  }

  async _uploadFile(file) {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(this.uploadApi, { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Upload failed");
    }
    return res.json();
  }

  _ensureTrustStrip() {
    let el = this.container.querySelector(".chat-trust-strip");
    if (el) return el;

    el = document.createElement("div");
    el.className = "chat-trust-strip loading";
    el.textContent = "Loading data trust...";

    const header = this.container.querySelector(".chat-widget-header");
    if (header && header.nextSibling) {
      this.container.insertBefore(el, header.nextSibling);
      return el;
    }

    if (this.messagesEl && this.messagesEl.parentNode) {
      this.messagesEl.parentNode.insertBefore(el, this.messagesEl);
      return el;
    }

    this.container.prepend(el);
    return el;
  }

  async _loadDataTrust() {
    if (!this.trustStripEl) return;
    this.trustStripEl.classList.add("loading");
    this.trustStripEl.textContent = "Loading data trust...";
    try {
      const res = await fetch(this.healthApi);
      if (!res.ok) throw new Error("health fetch failed");
      const data = await res.json();
      this._renderDataTrust(data);
    } catch (_err) {
      this.trustStripEl.classList.remove("loading");
      this.trustStripEl.innerHTML = "<span>Data trust unavailable.</span>";
    }
  }

  _renderDataTrust(data) {
    if (!this.trustStripEl || !data || !Array.isArray(data.components)) return;
    const important = ["square_orders", "daily_profitability", "deputy_rosters", "xero_cogs", "xero_financial_facts"];
    const pills = data.components
      .filter((c) => important.includes(c.source))
      .map((c) => {
        const latest = c.latest_date || "--";
        const age = c.age_days != null ? `${c.age_days}d` : "--";
        const messages = this._healthMessages(c);
        const note = messages.length ? `<span class="note">${this._escapeHtml(messages[0])}</span>` : "";
        return `
          <span class="chat-trust-pill">
            <span class="status-pill status-${c.status || "unknown"}">${c.status || "unknown"}</span>
            <span class="copy">
              <span>${this._sourceLabel(c.source)}</span>
              <span class="meta">${latest} · ${age}</span>
              ${note}
            </span>
          </span>
        `;
      })
      .join("");
    const score = data.score != null ? `${Math.round(data.score * 100)}%` : "--";
    this.trustStripEl.classList.remove("loading");
    this.trustStripEl.innerHTML = `
      <div class="chat-trust-overall">
        <span>Data Trust</span>
        <span class="status-pill status-${data.status || "unknown"}">${data.status || "unknown"}</span>
        <span>${score}</span>
      </div>
      <div class="chat-trust-pills">${pills}</div>
    `;
  }

  _sourceLabel(source) {
    const labels = {
      square_orders: "Square",
      daily_profitability: "Profit",
      deputy_rosters: "Deputy",
      xero_cogs: "Xero COGS",
      xero_financial_facts: "Xero Facts",
    };
    return labels[source] || source || "Unknown";
  }

  _healthMessages(component) {
    const messages = [];
    if (Array.isArray(component?.blockers)) messages.push(...component.blockers);
    if (Array.isArray(component?.limitations)) messages.push(...component.limitations);
    return messages.filter(Boolean);
  }

  async send() {
    const text = this.inputEl.value.trim();
    const hasFiles = this.pendingFiles.length > 0;
    if ((!text && !hasFiles) || this.streaming) return;

    // Hide suggestions
    if (this.suggestionsEl) {
      this.suggestionsEl.style.display = "none";
    }

    // Build user message text
    const userText = text || (hasFiles ? "Please analyze the uploaded document(s)." : "");

    // Add user message with file badges
    this.messages.push({ role: "user", content: userText });
    const userEl = this._renderMessage("user", userText);
    if (hasFiles) {
      const badgesHtml = this.pendingFiles.map((f) => {
        const icon = f.type.startsWith("image/") ? "🖼️" :
                     f.type === "application/pdf" ? "📄" : "📊";
        return `<span class="msg-file-badge">${icon} ${this._escapeHtml(f.name)}</span>`;
      }).join("");
      const bubble = userEl.querySelector(".msg-bubble");
      bubble.innerHTML += `<div class="msg-file-attachments">${badgesHtml}</div>`;
    }

    this.inputEl.value = "";
    this.inputEl.style.height = "auto";
    this.sendBtn.disabled = true;
    this.streaming = true;

    // Upload files first
    const documentIds = [];
    if (hasFiles) {
      const filesToUpload = [...this.pendingFiles];
      this.pendingFiles = [];
      this._renderFilePreview();

      const statusEl = this._renderMessage("assistant", "");
      const statusBubble = statusEl.querySelector(".msg-bubble");

      for (const file of filesToUpload) {
        statusBubble.innerHTML = `<div class="upload-status">Uploading ${this._escapeHtml(file.name)}...</div>`;
        try {
          const result = await this._uploadFile(file);
          documentIds.push(result.document_id);
        } catch (err) {
          statusBubble.innerHTML = `<div class="upload-status error">Upload failed: ${this._escapeHtml(err.message)}</div>`;
          this.streaming = false;
          this.sendBtn.disabled = false;
          return;
        }
      }
      // Remove status message, will be replaced by streaming response
      statusEl.remove();
    }

    // Add assistant placeholder with typing indicator
    const assistantEl = this._renderMessage("assistant", "");
    const bubbleEl = assistantEl.querySelector(".msg-bubble");
    if (documentIds.length > 0) {
      bubbleEl.innerHTML = '<div class="upload-status">Analyzing document...</div>';
    } else {
      bubbleEl.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
    }

    // Stream response
    let fullContent = "";
    let latestPayload = null;
    try {
      const res = await fetch(this.api, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: this.messages,
          document_ids: documentIds,
        }),
      });

      if (!res.ok) {
        const errText = await res.text().catch(() => "");
        fullContent = `*Request failed (${res.status}). ${this._escapeHtml(errText || "Please try again.")}*`;
        bubbleEl.innerHTML = this._renderMarkdown(fullContent);
        this.messages.push({ role: "assistant", content: fullContent });
        this.streaming = false;
        this.sendBtn.disabled = false;
        this.inputEl.focus();
        return;
      }

      if (!res.body) {
        fullContent = "*No response body received from chat API.*";
        bubbleEl.innerHTML = this._renderMarkdown(fullContent);
        this.messages.push({ role: "assistant", content: fullContent });
        this.streaming = false;
        this.sendBtn.disabled = false;
        this.inputEl.focus();
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop(); // Keep incomplete line

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const jsonStr = line.slice(6);
          try {
            const data = JSON.parse(jsonStr);
            if (data.done) break;
            if (data.error) {
              fullContent += `\n\n*Error: ${data.error}*`;
              break;
            }
            // Handle extraction events
            if (data.extraction) {
              const ext = data.extraction;
              this._showExtractionNotice(ext);
              // Switch from "analyzing" to typing indicator
              if (!fullContent) {
                bubbleEl.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
              }
              continue;
            }
            if (data.extraction_status) {
              bubbleEl.innerHTML = '<div class="upload-status">Analyzing document...</div>';
              continue;
            }
            if (data.extraction_error) {
              fullContent += `\n\n*Document extraction error: ${data.extraction_error}*`;
              continue;
            }
            if (
              "final_answer" in data ||
              "warnings" in data ||
              "follow_up_questions" in data ||
              "blocked" in data
            ) {
              latestPayload = data;
              fullContent = data.final_answer || data.content || "";
              bubbleEl.innerHTML = this._renderAssistantPayload(data);
              this._scrollToBottom();
              continue;
            }
            if (data.content) {
              fullContent += data.content;
              bubbleEl.innerHTML = this._renderMarkdown(fullContent);
              this._scrollToBottom();
            }
          } catch (e) {
            // Skip malformed JSON
          }
        }
      }
    } catch (err) {
      fullContent = "*Sorry, something went wrong. Please try again.*";
      bubbleEl.innerHTML = this._renderMarkdown(fullContent);
    }

    // Store assistant response
    if (fullContent) {
      if (latestPayload) {
        const warningList = latestPayload.warnings || [];
        const followUpList = latestPayload.follow_up_questions || [];
        const hasMeta = warningList.length > 0 || followUpList.length > 0;
        const assistantContent = latestPayload.blocked
          ? (latestPayload.final_answer || latestPayload.content || fullContent)
          : (
              hasMeta
                ? (latestPayload.draft_answer || latestPayload.final_answer || latestPayload.content || fullContent)
                : (latestPayload.final_answer || latestPayload.content || latestPayload.draft_answer || fullContent)
            );
        this.messages.push({
          role: "assistant",
          content: assistantContent,
          warnings: warningList,
          follow_up_questions: followUpList,
          follow_up_hint: latestPayload.follow_up_hint || null,
          blocked: Boolean(latestPayload.blocked),
          block_reason: latestPayload.block_reason || null,
        });
      } else {
        this.messages.push({ role: "assistant", content: fullContent });
      }
    }

    this.streaming = false;
    this.sendBtn.disabled = false;
    this.inputEl.focus();
    this._scrollToBottom();
  }

  _showExtractionNotice(ext) {
    const notice = document.createElement("div");
    notice.className = "extraction-notice";
    let text = `📋 ${ext.summary || "Document processed"}`;
    if (ext.is_cogs_document && ext.items_count > 0) {
      text += ` — ${ext.items_count} item cost(s) updated`;
    }
    if (ext.events_count > 0) {
      text += ` — ${ext.events_count} event(s) added`;
    }
    notice.textContent = text;
    document.body.appendChild(notice);
    setTimeout(() => notice.classList.add("visible"), 10);
    setTimeout(() => {
      notice.classList.remove("visible");
      setTimeout(() => notice.remove(), 300);
    }, 5000);
  }

  _renderMessage(role, content) {
    const el = document.createElement("div");
    el.className = `chat-msg ${role}`;

    const avatar = role === "user" ? "You" : "AI";
    const avatarEl = `<div class="msg-avatar">${avatar}</div>`;
    const bubbleHtml = content ? this._renderMarkdown(content) : "";

    el.innerHTML = `${avatarEl}<div class="msg-bubble">${bubbleHtml}</div>`;
    this.messagesEl.appendChild(el);
    this._scrollToBottom();
    return el;
  }

  _renderAssistantPayload(payload) {
    const warnings = Array.isArray(payload?.warnings) ? payload.warnings : [];
    const followUps = Array.isArray(payload?.follow_up_questions) ? payload.follow_up_questions : [];
    const followUpHint = payload?.follow_up_hint || "";
    const blocked = Boolean(payload?.blocked);
    const hasMeta = warnings.length > 0 || followUps.length > 0;
    const answer = blocked
      ? (payload?.final_answer || payload?.content || "")
      : (
          hasMeta
            ? (payload?.draft_answer || payload?.final_answer || payload?.content || "")
            : (payload?.final_answer || payload?.content || payload?.draft_answer || "")
        );

    const parts = [];
    if (answer) {
      parts.push(`<div class="assistant-answer">${this._renderMarkdown(answer)}</div>`);
    }
    if (warnings.length) {
      parts.push(this._renderAssistantMetaBlock("Confidence note", warnings, "warning"));
    }
    if (followUps.length) {
      let followUpHtml = this._renderAssistantMetaBlock(
        blocked ? "What needs fixing first" : "To improve this further",
        followUps,
        "follow-up",
      );
      if (followUpHint) {
        followUpHtml += `<div class="assistant-meta-hint">${this._renderMarkdown(followUpHint)}</div>`;
      }
      parts.push(followUpHtml);
    }
    return parts.join("");
  }

  _renderAssistantMetaBlock(title, items, kind) {
    const listItems = items
      .map((item) => `<li>${this._escapeHtml(item)}</li>`)
      .join("");
    return `
      <div class="assistant-meta-block ${kind}">
        <div class="assistant-meta-title">${this._escapeHtml(title)}</div>
        <ul>${listItems}</ul>
      </div>
    `;
  }

  _scrollToBottom() {
    requestAnimationFrame(() => {
      this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    });
  }

  // --- Simple Markdown Renderer ---

  _renderMarkdown(text) {
    if (!text) return "";

    let html = this._escapeHtml(text);

    // Code blocks (``` ... ```)
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
      return `<pre><code>${code.trim()}</code></pre>`;
    });

    // Inline code
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

    // Tables
    html = this._renderTables(html);

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

    // Italic
    html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "<em>$1</em>");

    // Unordered lists
    html = html.replace(/((?:^|\n)- .+(?:\n- .+)*)/g, (block) => {
      const items = block.trim().split("\n").map((line) =>
        `<li>${line.replace(/^- /, "")}</li>`
      ).join("");
      return `<ul>${items}</ul>`;
    });

    // Ordered lists
    html = html.replace(/((?:^|\n)\d+\. .+(?:\n\d+\. .+)*)/g, (block) => {
      const items = block.trim().split("\n").map((line) =>
        `<li>${line.replace(/^\d+\.\s*/, "")}</li>`
      ).join("");
      return `<ol>${items}</ol>`;
    });

    // Headers (###, ##, #)
    html = html.replace(/^### (.+)$/gm, "<strong>$1</strong>");
    html = html.replace(/^## (.+)$/gm, "<strong>$1</strong>");
    html = html.replace(/^# (.+)$/gm, "<strong>$1</strong>");

    // Paragraphs (double newline)
    html = html.replace(/\n\n/g, "</p><p>");
    html = `<p>${html}</p>`;

    // Single newlines → <br> (but not inside pre/table)
    html = html.replace(/(?<!<\/li>|<\/tr>|<\/th>|<\/td>|<\/pre>|<\/table>|<\/ul>|<\/ol>)\n/g, "<br>");

    // Clean up empty paragraphs
    html = html.replace(/<p>\s*<\/p>/g, "");

    return html;
  }

  _renderTables(html) {
    // Match markdown tables: header row + separator + data rows
    const tableRegex = /(?:^|\n)(\|.+\|)\n(\|[\s:|-]+\|)\n((?:\|.+\|\n?)+)/g;

    return html.replace(tableRegex, (_, headerRow, sepRow, bodyRows) => {
      const headers = headerRow.split("|").filter((c) => c.trim()).map((c) => c.trim());
      const rows = bodyRows.trim().split("\n").map((row) =>
        row.split("|").filter((c) => c.trim()).map((c) => c.trim())
      );

      let table = "<table><thead><tr>";
      for (const h of headers) {
        table += `<th>${h}</th>`;
      }
      table += "</tr></thead><tbody>";
      for (const row of rows) {
        table += "<tr>";
        for (const cell of row) {
          table += `<td>${cell}</td>`;
        }
        table += "</tr>";
      }
      table += "</tbody></table>";
      return table;
    });
  }

  _escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }
}


// ============================================================
// Standalone Page Init
// ============================================================

function initChatPage() {
  const container = document.querySelector(".chat-body");
  if (!container) return;

  const params = new URLSearchParams(window.location.search);
  const siteId = params.get("site_id") || "";

  if (!siteId) {
    container.innerHTML = `<div class="chat-suggestions">
      <h3>No site selected</h3>
      <p style="color:var(--muted);font-size:14px;">Add <code>?site_id=YOUR_SITE_ID</code> to the URL.</p>
    </div>`;
    return;
  }

  // Set up clock
  const clockTime = document.getElementById("clock-time");
  const clockDate = document.getElementById("clock-date");
  if (clockTime && clockDate) {
    function updateClock() {
      const now = new Date();
      clockTime.textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      clockDate.textContent = now.toLocaleDateString([], { weekday: "long", year: "numeric", month: "short", day: "numeric" });
    }
    updateClock();
    setInterval(updateClock, 1000);
  }

  // Fetch site name
  fetch(`/api/sites/${siteId}/status`)
    .then((r) => r.json())
    .then((site) => {
      const nameEl = document.getElementById("site-name");
      if (nameEl) nameEl.textContent = site.name || "Clubhouse Autopilot";
      const subEl = document.getElementById("site-sub");
      if (subEl) subEl.textContent = "Chat Assistant";
    })
    .catch(() => {});

  new ChatUI(container, siteId);
}


// ============================================================
// Dashboard Widget Init
// ============================================================

function initChatWidget(siteId) {
  const btn = document.getElementById("chat-widget-btn");
  const panel = document.getElementById("chat-widget-panel");
  if (!btn || !panel || !siteId) return;

  let chat = null;

  btn.addEventListener("click", () => {
    const isOpen = panel.classList.contains("visible");
    if (isOpen) {
      panel.classList.remove("visible");
      btn.classList.remove("open");
    } else {
      panel.classList.add("visible");
      btn.classList.add("open");
      if (!chat) {
        chat = new ChatUI(panel, siteId);
      }
      const input = panel.querySelector(".chat-input");
      if (input) input.focus();
    }
  });

  const closeBtn = panel.querySelector(".chat-widget-close");
  if (closeBtn) {
    closeBtn.addEventListener("click", () => {
      panel.classList.remove("visible");
      btn.classList.remove("open");
    });
  }
}


// Auto-init standalone page
if (document.querySelector(".chat-page")) {
  initChatPage();
}
