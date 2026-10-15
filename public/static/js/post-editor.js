/* Inline editor for a single blog post.
 *
 * The page renders the post as an ordered list of blocks. In edit mode the text
 * becomes contenteditable and every block gains a drag handle, so the post can
 * be rearranged directly on the page and saved with fetch().
 */
(function () {
  var cfg = window.POST_EDITOR;
  if (!cfg) return;

  var body = document.getElementById("post-body");
  var title = document.querySelector(".post-title");
  var standfirst = document.querySelector(".post-standfirst");
  var toggle = document.getElementById("inline-toggle");
  if (!body || !toggle) return;

  var editing = false;
  var dirty = false;
  var dragEl = null;

  /* ---------------------------------------------------------------- status */

  function setStatus(text, cls) {
    var el = document.getElementById("inline-status");
    if (!el) return;
    el.textContent = text;
    el.className = "inline-status" + (cls ? " " + cls : "");
  }

  function markDirty() {
    dirty = true;
    setStatus("Unsaved changes", "is-dirty");
  }

  function clearDirty(at) {
    dirty = false;
    setStatus("Saved at " + (at || ""), "is-saved");
  }

  /* ---------------------------------------------------------------- blocks */

  function makeTextBlock(html) {
    var block = document.createElement("div");
    block.className = "post-block";
    block.setAttribute("data-type", "text");
    block.innerHTML = html || "<p></p>";
    return block;
  }

  function makeImageBlock(ref, url) {
    var block = document.createElement("figure");
    block.className = "post-block post-block-image";
    block.setAttribute("data-type", "image");
    block.setAttribute("data-ref", ref);

    var img = document.createElement("img");
    img.src = url;
    img.alt = "";
    block.appendChild(img);
    return block;
  }

  function decorate(block) {
    if (block.querySelector(".block-handle")) return;

    var handle = document.createElement("button");
    handle.type = "button";
    handle.className = "block-handle";
    handle.title = "Drag to move this block";
    handle.setAttribute("aria-label", "Drag to move");
    handle.innerHTML = "&#9776;";

    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "block-remove";
    remove.title = "Delete this block";
    remove.setAttribute("aria-label", "Delete block");
    remove.innerHTML = "&times;";

    remove.addEventListener("click", function () {
      if (block.dataset.type === "image" ||
          block.textContent.trim() === "") {
        block.remove();
      } else if (window.confirm("Delete this block?")) {
        block.remove();
      }
      markDirty();
    });

    // A contenteditable block cannot be dragged, so only allow dragging while
    // the handle is held down.
    handle.addEventListener("mousedown", function () {
      block.draggable = true;
    });
    handle.addEventListener("touchstart", function () {
      block.draggable = true;
    });

    block.appendChild(handle);
    block.appendChild(remove);
    attachDrag(block);
  }

  function setEditable(el, on) {
    if (!el) return;
    if (on) {
      el.setAttribute("contenteditable", "true");
      el.addEventListener("input", markDirty);
    } else {
      el.removeAttribute("contenteditable");
    }
  }

  /* ------------------------------------------------------------ edit mode */

  function enterEditMode() {
    editing = true;
    document.body.classList.add("inline-editing");
    toggle.textContent = "Done editing";

    setEditable(title, true);
    setEditable(standfirst, true);
    Array.prototype.forEach.call(
      body.querySelectorAll('.post-block[data-type="text"]'),
      function (block) {
        setEditable(block, true);
        decorate(block);
      }
    );
    Array.prototype.forEach.call(
      body.querySelectorAll('.post-block[data-type="image"]'),
      decorate
    );

    document.getElementById("inline-bar").hidden = false;
    setStatus(dirty ? "Unsaved changes" : "No changes yet",
              dirty ? "is-dirty" : "");
  }

  function exitEditMode() {
    editing = false;
    document.body.classList.remove("inline-editing");
    toggle.textContent = "Edit this page";

    setEditable(title, false);
    setEditable(standfirst, false);
    Array.prototype.forEach.call(
      body.querySelectorAll('[contenteditable="true"]'),
      function (el) {
        el.removeAttribute("contenteditable");
      }
    );
    document.getElementById("inline-bar").hidden = true;
  }

  toggle.addEventListener("click", function () {
    if (editing) exitEditMode();
    else enterEditMode();
  });

  window.addEventListener("beforeunload", function (event) {
    if (dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  /* ---------------------------------------------------------------- drag */

  function clearDropHints() {
    Array.prototype.forEach.call(
      body.querySelectorAll(".drag-over"),
      function (el) { el.classList.remove("drag-over"); }
    );
  }

  function attachDrag(block) {
    block.addEventListener("dragstart", function (event) {
      dragEl = block;
      block.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      // Some browsers refuse to start a drag unless data is set.
      event.dataTransfer.setData("text/plain", "block");
    });

    block.addEventListener("dragend", function () {
      block.classList.remove("is-dragging");
      block.draggable = false;
      clearDropHints();
      dragEl = null;
    });

    block.addEventListener("dragover", function (event) {
      if (!dragEl || dragEl === block) return;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = "move";

      var rect = block.getBoundingClientRect();
      var above = event.clientY - rect.top < rect.height / 2;
      clearDropHints();
      block.classList.add("drag-over");
      block.setAttribute("data-drop-above", above ? "1" : "0");
    });

    block.addEventListener("drop", function (event) {
      if (!dragEl || dragEl === block) return;
      event.preventDefault();
      event.stopPropagation();

      if (block.getAttribute("data-drop-above") === "1") {
        body.insertBefore(dragEl, block);
      } else {
        body.insertBefore(dragEl, block.nextSibling);
      }
      clearDropHints();
      markDirty();
    });
  }

  // Releasing the mouse anywhere stops a drag from starting.
  document.addEventListener("mouseup", function () {
    Array.prototype.forEach.call(
      body.querySelectorAll(".post-block"),
      function (el) { el.draggable = false; }
    );
  });

  /* -------------------------------------------------------------- upload */

  function uploadImage(file, afterEl) {
    var data = new FormData();
    data.append("image", file);
    setStatus("Uploading image…", "");

    fetch(cfg.uploadUrl, {
      method: "POST",
      body: data,
      headers: { "X-CSRF-Token": cfg.csrf },
    })
      .then(function (response) {
        return response.json().then(function (json) {
          return { ok: response.ok, json: json };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.json.ok) {
          throw new Error(result.json.error || "Upload failed.");
        }
        var block = makeImageBlock(result.json.ref, result.json.url);
        decorate(block);
        if (afterEl && afterEl.parentNode === body) {
          body.insertBefore(block, afterEl.nextSibling);
        } else {
          body.appendChild(block);
        }
        markDirty();
      })
      .catch(function (error) {
        setStatus(error.message, "is-error");
      });
  }

  // Dropping an image file straight onto the page.
  body.addEventListener("dragover", function (event) {
    if (dragEl) return;
    if (event.dataTransfer.types &&
        Array.prototype.indexOf.call(event.dataTransfer.types, "Files") !== -1) {
      event.preventDefault();
      document.body.classList.add("drop-active");
    }
  });

  body.addEventListener("dragleave", function () {
    document.body.classList.remove("drop-active");
  });

  body.addEventListener("drop", function (event) {
    if (dragEl) return;
    var file = event.dataTransfer.files && event.dataTransfer.files[0];
    if (!file) return;
    event.preventDefault();
    document.body.classList.remove("drop-active");
    uploadImage(file, null);
  });

  /* ------------------------------------------------------------ add block */

  var addText = document.getElementById("inline-add-text");
  if (addText) {
    addText.addEventListener("click", function () {
      var block = makeTextBlock("<p>New paragraph — click and type.</p>");
      body.appendChild(block);
      setEditable(block, true);
      decorate(block);
      block.focus();
      markDirty();
    });
  }

  var addImage = document.getElementById("inline-add-image");
  var fileInput = document.getElementById("inline-file");
  if (addImage && fileInput) {
    addImage.addEventListener("click", function () { fileInput.click(); });
    fileInput.addEventListener("change", function () {
      if (fileInput.files[0]) uploadImage(fileInput.files[0], null);
      fileInput.value = "";
    });
  }

  /* ---------------------------------------------------------------- save */

  function collect() {
    var blocks = [];
    Array.prototype.forEach.call(
      body.querySelectorAll(".post-block"),
      function (el) {
        if (el.getAttribute("data-type") === "text") {
          blocks.push({ type: "text", html: el.innerHTML.trim() });
        } else {
          blocks.push({ type: "image", ref: el.getAttribute("data-ref") });
        }
      }
    );
    return {
      heading: title ? title.innerHTML.trim() : "",
      discription: standfirst ? standfirst.innerHTML.trim() : "",
      blocks: blocks,
    };
  }

  function save() {
    setStatus("Saving…", "");
    fetch(cfg.saveUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": cfg.csrf,
      },
      body: JSON.stringify(collect()),
    })
      .then(function (response) {
        var type = response.headers.get("content-type") || "";
        // An expired session redirects to the login page, which is HTML.
        if (type.indexOf("application/json") === -1) {
          throw new Error("Your session expired — please sign in again.");
        }
        return response.json();
      })
      .then(function (json) {
        if (!json.ok) throw new Error(json.error || "Could not save.");

        // A brand-new post takes its URL from the title on the first save.
        if (json.url && json.url !== cfg.slug) {
          cfg.slug = json.url;
          cfg.saveUrl = cfg.saveUrl.replace(/[^/]+$/, json.url);
          if (window.history.replaceState) {
            window.history.replaceState({}, "", "/blog/" + json.url);
          }
        }
        clearDirty(json.saved_at);
      })
      .catch(function (error) {
        setStatus(error.message, "is-error");
      });
  }

  var saveButton = document.getElementById("inline-save");
  if (saveButton) saveButton.addEventListener("click", save);

  var exitButton = document.getElementById("inline-exit");
  if (exitButton) {
    exitButton.addEventListener("click", function () {
      if (!dirty || window.confirm("Discard unsaved changes?")) {
        dirty = false;
        exitEditMode();
      }
    });
  }

  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      if (editing) save();
    }
  });

  // Arriving from "+ New post": open ready to type, with the title selected so
  // the placeholder is replaced the moment you start writing.
  if (cfg.autoEdit) {
    enterEditMode();
    if (title) {
      title.focus();
      var range = document.createRange();
      range.selectNodeContents(title);
      var selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
  }
})();
