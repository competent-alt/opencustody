"""Desktop GUI (tkinter ships with Python, so there is no third-party GUI code to audit)."""

from __future__ import annotations

import os
import queue
import secrets
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from . import __version__, config, vault
from .amounts import fmt, to_base_units
from .chains import build_chains, build_monero
from .chains.base import Asset, Chain
from .keys import Keyring, is_valid_mnemonic, new_mnemonic, normalize_mnemonic
from .paths import config_path, vault_path

PAD = 12
MONO = ("Consolas", 10)
_rng = secrets.SystemRandom()


def err_box(e: BaseException) -> None:
    messagebox.showerror("Error", str(e) or e.__class__.__name__)


def open_url(url: str) -> None:
    if url.startswith("https://"):
        webbrowser.open(url)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"OpenCustody {__version__}")
        self.geometry("1040x660")
        self.minsize(880, 580)
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Big.TLabel", font=("Segoe UI", 12))
        style.configure("Warn.TLabel", foreground="#b00020")
        config.ensure_file()
        self.cfg = config.load()
        self.keyring: Keyring | None = None
        self.chains: list[Chain] = []
        self._q: queue.Queue = queue.Queue()
        self._last_activity = time.monotonic()
        self._frame: ttk.Frame | None = None
        self._session = 0  # bumps on lock so late background results are dropped
        for seq in ("<Any-KeyPress>", "<Any-Button>", "<Motion>"):
            self.bind_all(seq, self._touch, add="+")
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.after(100, self._pump)
        self.after(5000, self._check_autolock)
        self.show(UnlockFrame if vault_path().exists() else WelcomeFrame)

    # ---- plumbing ---------------------------------------------------------------
    def show(self, cls, *args):
        if self._frame is not None:
            self._frame.destroy()
        self._frame = cls(self, *args)
        self._frame.pack(fill="both", expand=True, padx=PAD, pady=PAD)

    def run_bg(self, fn, on_ok=None, on_err=err_box):
        """Run fn in a worker thread; deliver the result on the Tk thread."""
        session = self._session

        def work():
            try:
                self._q.put((session, on_ok, fn(), None))
            except Exception as e:  # surfaced to the user
                self._q.put((session, on_err, None, e))

        threading.Thread(target=work, daemon=True).start()

    def _pump(self):
        try:
            while True:
                session, cb, res, err = self._q.get_nowait()
                if session != self._session or cb is None:
                    continue
                try:
                    cb(err if err is not None else res)
                except tk.TclError:
                    pass  # widget was destroyed meanwhile
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _touch(self, _event=None):
        self._last_activity = time.monotonic()

    def _check_autolock(self):
        mins = float(self.cfg.get("auto_lock_minutes", 10) or 0)
        if self.keyring and mins > 0 and time.monotonic() - self._last_activity > mins * 60:
            self.lock()
        self.after(5000, self._check_autolock)

    # ---- session ----------------------------------------------------------------
    def unlock_with(self, s: vault.Secrets):
        self.keyring = Keyring(s.mnemonic, s.passphrase)
        self.cfg = config.load()
        self.chains, errors = build_chains(self.cfg, self.keyring)
        self.show(MainFrame, errors)

    def lock(self):
        self._session += 1
        for w in self.winfo_children():
            if isinstance(w, tk.Toplevel):
                w.destroy()  # close any open send/confirm dialogs
        chains, self.chains = self.chains, []
        for c in chains:
            try:
                c.close()
            except Exception:  # nosec B110 - best-effort shutdown
                pass
        if self.keyring:
            self.keyring.wipe()
        self.keyring = None
        self.show(UnlockFrame)

    def _quit(self):
        if self.keyring:
            self.lock()
        self.destroy()


# ==== onboarding ===================================================================
class WelcomeFrame(ttk.Frame):
    def __init__(self, app: App):
        super().__init__(app)
        ttk.Label(self, text="OpenCustody", style="Title.TLabel").pack(pady=(60, 6))
        ttk.Label(self, text="Self-custodial wallet for Bitcoin, Litecoin, Ethereum & EVM chains, "
                             "Solana, Tron, TON and Monero.", style="Big.TLabel").pack()
        ttk.Label(self, text="Your keys never leave this computer. Nobody, including the developers, "
                             "can recover your funds without your recovery phrase.").pack(pady=(4, 30))
        ttk.Button(self, text="Create a new wallet", width=32,
                   command=lambda: app.show(ShowMnemonicFrame, new_mnemonic())).pack(pady=6)
        ttk.Button(self, text="Restore from recovery phrase", width=32,
                   command=lambda: app.show(RestoreFrame)).pack(pady=6)


class ShowMnemonicFrame(ttk.Frame):
    def __init__(self, app: App, mnemonic: str):
        super().__init__(app)
        ttk.Label(self, text="Your recovery phrase", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, text="Write these 24 words on paper, in order, and store them offline. "
                             "Never type them into a website, photo, cloud note, or chat. "
                             "Anyone with these words controls your funds.", wraplength=900,
                  style="Warn.TLabel").pack(anchor="w", pady=(4, 12))
        grid = ttk.Frame(self)
        grid.pack(anchor="w")
        for i, w in enumerate(mnemonic.split()):
            ttk.Label(grid, text=f"{i + 1:>2}. {w}", font=("Consolas", 13), width=18).grid(
                row=i % 8, column=i // 8, sticky="w", padx=8, pady=3)
        ok = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="I have written down all 24 words in order", variable=ok).pack(anchor="w", pady=12)
        bar = ttk.Frame(self)
        bar.pack(anchor="w")
        ttk.Button(bar, text="Back", command=lambda: app.show(WelcomeFrame)).pack(side="left")
        ttk.Button(bar, text="Continue",
                   command=lambda: app.show(VerifyMnemonicFrame, mnemonic) if ok.get()
                   else messagebox.showinfo("Recovery phrase", "Please write the words down first.")
                   ).pack(side="left", padx=8)


class VerifyMnemonicFrame(ttk.Frame):
    def __init__(self, app: App, mnemonic: str):
        super().__init__(app)
        words = mnemonic.split()
        positions = sorted(_rng.sample(range(len(words)), 3))
        ttk.Label(self, text="Confirm your backup", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, text="Enter the requested words from your paper backup.").pack(anchor="w", pady=(4, 12))
        entries = []
        for p in positions:
            row = ttk.Frame(self)
            row.pack(anchor="w", pady=3)
            ttk.Label(row, text=f"Word #{p + 1}", width=10).pack(side="left")
            e = ttk.Entry(row, width=24)
            e.pack(side="left")
            entries.append((p, e))

        def check():
            if all(e.get().strip().lower() == words[p] for p, e in entries):
                app.show(SetPasswordFrame, mnemonic, True)
            else:
                messagebox.showerror("Mismatch", "Those words don't match. Check your backup.")

        bar = ttk.Frame(self)
        bar.pack(anchor="w", pady=12)
        ttk.Button(bar, text="Show words again", command=lambda: app.show(ShowMnemonicFrame, mnemonic)).pack(side="left")
        ttk.Button(bar, text="Confirm", command=check).pack(side="left", padx=8)


class RestoreFrame(ttk.Frame):
    def __init__(self, app: App):
        super().__init__(app)
        ttk.Label(self, text="Restore wallet", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, text="Enter your 12 or 24-word BIP-39 recovery phrase, separated by spaces.").pack(anchor="w", pady=4)
        txt = tk.Text(self, height=4, width=90, font=("Consolas", 12), wrap="word")
        txt.pack(anchor="w", pady=6)
        bar = ttk.Frame(self)
        bar.pack(anchor="w", pady=8)

        def go():
            m = normalize_mnemonic(txt.get("1.0", "end"))
            if not is_valid_mnemonic(m):
                messagebox.showerror("Invalid", "That is not a valid BIP-39 phrase (check spelling and word order).")
                return
            txt.delete("1.0", "end")
            app.show(SetPasswordFrame, m, False)

        ttk.Button(bar, text="Back", command=lambda: app.show(WelcomeFrame)).pack(side="left")
        ttk.Button(bar, text="Continue", command=go).pack(side="left", padx=8)


class SetPasswordFrame(ttk.Frame):
    def __init__(self, app: App, mnemonic: str, is_new: bool):
        super().__init__(app)
        ttk.Label(self, text="Set a password", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, text=f"This encrypts the wallet on this computer (at least {vault.MIN_PASSWORD_LEN} characters). "
                             "It is NOT a backup: if you forget it, restore from your recovery phrase.",
                  wraplength=900).pack(anchor="w", pady=(4, 12))
        form = ttk.Frame(self)
        form.pack(anchor="w")
        pw1, pw2, pp = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for r, (label, var) in enumerate((("Password", pw1), ("Repeat password", pw2))):
            ttk.Label(form, text=label, width=18).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=var, show="•", width=36).grid(row=r, column=1, pady=3)
        use_pp = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="Advanced: add a BIP-39 passphrase (25th word)", variable=use_pp).pack(anchor="w", pady=(10, 0))
        pp_row = ttk.Frame(self)
        pp_row.pack(anchor="w")
        ttk.Label(pp_row, text="Passphrase", width=18).pack(side="left")
        ttk.Entry(pp_row, textvariable=pp, show="•", width=36).pack(side="left")
        ttk.Label(self, text="A passphrase creates a completely different wallet. You need BOTH the words and the "
                             "passphrase to recover; losing it means losing the funds.", wraplength=900).pack(anchor="w", pady=4)
        status = ttk.Label(self, text="")
        status.pack(anchor="w", pady=6)
        btn = ttk.Button(self, text="Create encrypted wallet")
        btn.pack(anchor="w")

        def done(s: vault.Secrets):
            app.unlock_with(s)

        def failed(e):
            btn.state(["!disabled"])
            status.config(text="")
            err_box(e)

        def submit():
            if pw1.get() != pw2.get():
                messagebox.showerror("Password", "Passwords don't match.")
                return
            try:
                vault.check_password_strength(pw1.get())
            except vault.VaultError as e:
                err_box(e)
                return
            s = vault.Secrets(mnemonic, pp.get() if use_pp.get() else "")
            meta = {"created_new": is_new, "created_at": int(time.time())}
            btn.state(["disabled"])
            status.config(text="Encrypting (this takes a second on purpose)…")
            password = pw1.get()

            def work():
                vault.save(vault_path(), s, password, meta)
                return s

            app.run_bg(work, done, failed)

        btn.config(command=submit)


class UnlockFrame(ttk.Frame):
    def __init__(self, app: App):
        super().__init__(app)
        ttk.Label(self, text="Unlock wallet", style="Title.TLabel").pack(pady=(100, 12))
        pw = tk.StringVar()
        e = ttk.Entry(self, textvariable=pw, show="•", width=36)
        e.pack()
        e.focus_set()
        status = ttk.Label(self, text="")
        status.pack(pady=6)

        def ok(s):
            app.unlock_with(s)

        def bad(err):
            status.config(text="")
            err_box(err)

        def submit(_evt=None):
            status.config(text="Decrypting…")
            p = pw.get()
            pw.set("")
            app.run_bg(lambda: vault.load(vault_path(), p), ok, bad)

        e.bind("<Return>", submit)
        ttk.Button(self, text="Unlock", command=submit).pack(pady=6)
        ttk.Label(self, text=f"Vault: {vault_path()}", foreground="#666").pack(side="bottom")


# ==== main wallet ==================================================================
class MainFrame(ttk.Frame):
    def __init__(self, app: App, errors: list[str]):
        super().__init__(app)
        self.app = app
        self.rows: dict[str, tuple[Chain, Asset]] = {}
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Wallet", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Lock", command=app.lock).pack(side="right")
        ttk.Button(top, text="Settings", command=self.settings).pack(side="right", padx=6)
        ttk.Button(top, text="Refresh balances", command=self.refresh).pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, pady=PAD)
        left = ttk.Frame(body)
        self.tree = ttk.Treeview(left, columns=("asset", "balance"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Network")
        self.tree.heading("asset", text="Asset")
        self.tree.heading("balance", text="Balance")
        self.tree.column("#0", width=170)
        self.tree.column("asset", width=80)
        self.tree.column("balance", width=170, anchor="e")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.on_select())
        body.add(left, weight=1)

        right = ttk.Frame(body, padding=(PAD, 0))
        self.sel_title = ttk.Label(right, text="Select an account", style="Big.TLabel")
        self.sel_title.pack(anchor="w")
        ttk.Label(right, text="Receive address:").pack(anchor="w", pady=(12, 2))
        self.addr = tk.Text(right, height=3, width=52, font=("Consolas", 12), wrap="char", relief="flat")
        self.addr.pack(anchor="w", fill="x")
        self.addr.config(state="disabled")
        ttk.Label(right, text="Always compare the first and last characters after pasting an address.",
                  foreground="#666").pack(anchor="w")
        btns = ttk.Frame(right)
        btns.pack(anchor="w", pady=10)
        self.b_copy = ttk.Button(btns, text="Copy address", command=self.copy)
        self.b_copy.pack(side="left")
        self.b_send = ttk.Button(btns, text="Send…", command=self.send)
        self.b_send.pack(side="left", padx=6)
        self.b_exp = ttk.Button(btns, text="View in explorer", command=self.explorer)
        self.b_exp.pack(side="left")
        self.notes = ttk.Label(right, text="", wraplength=440, justify="left")
        self.notes.pack(anchor="w", pady=10)
        body.add(right, weight=1)

        self.populate()
        msgs = list(errors)
        if not app.cfg["monero"].get("enabled"):
            msgs.append("Monero is off: set monero.enabled, wallet_rpc_path and daemon in Settings.")
        else:
            self.start_monero()
        self.notes.config(text="\n".join(msgs))
        self.refresh()

    def populate(self):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        for ci, chain in enumerate(self.app.chains):
            parent = None
            for ai, asset in enumerate(chain.assets()):
                iid = f"{ci}:{ai}"
                self.rows[iid] = (chain, asset)
                if parent is None:
                    self.tree.insert("", "end", iid=iid, text=chain.name, values=(asset.symbol, "…"), open=True)
                    parent = iid
                else:
                    self.tree.insert(parent, "end", iid=iid, text="  token", values=(asset.symbol, "…"))

    def start_monero(self):
        app = self.app
        cfg = app.cfg

        def work():
            meta = vault.read_metadata(vault_path())
            height = int(cfg["monero"].get("restore_height") or 0)
            if meta.get("created_new") and not height:
                from .chains.monero import daemon_height
                tip = daemon_height(cfg["monero"]["daemon"])
                age_blocks = (int(time.time()) - int(meta.get("created_at", 0))) // 120
                height = max(0, tip - age_blocks - 2000)
            return build_monero(cfg, app.keyring, height)

        def ok(chain):
            app.chains.append(chain)
            self.populate()
            self.refresh()

        def bad(e):
            self.notes.config(text=f"Monero: {e}")

        self.notes.config(text="Starting Monero wallet…")
        self.app.run_bg(work, ok, bad)

    def selected(self) -> tuple[Chain, Asset] | None:
        sel = self.tree.selection()
        return self.rows.get(sel[0]) if sel else None

    def on_select(self):
        s = self.selected()
        if not s:
            return
        chain, asset = s
        self.sel_title.config(text=f"{chain.name} · {asset.symbol}")
        self.addr.config(state="normal")
        self.addr.delete("1.0", "end")
        self.addr.insert("1.0", chain.address())
        self.addr.config(state="disabled")
        if asset.contract:
            self.notes.config(text=f"Token contract: {asset.contract}\nYou need some "
                                   f"{chain.native.symbol} on {chain.name} to pay network fees.")
        else:
            self.notes.config(text="")

    def refresh(self):
        for iid, (chain, asset) in list(self.rows.items()):
            self.tree.set(iid, "balance", "…")

            def ok(v, iid=iid, asset=asset):
                if self.tree.exists(iid):
                    self.tree.set(iid, "balance", fmt(v, asset.decimals))

            def bad(e, iid=iid):
                if self.tree.exists(iid):
                    self.tree.set(iid, "balance", "error")
                    self.notes.config(text=str(e)[:300])

            self.app.run_bg(lambda c=chain, a=asset: c.balance(a), ok, bad)

    def copy(self):
        s = self.selected()
        if s:
            self.clipboard_clear()
            self.clipboard_append(s[0].address())

    def explorer(self):
        s = self.selected()
        if s:
            open_url(s[0].address_url())

    def send(self):
        s = self.selected()
        if s:
            SendDialog(self.app, *s)

    def settings(self):
        messagebox.showinfo(
            "Settings",
            f"Settings live in:\n{config_path()}\n\nEdit endpoints, tokens, auto-lock and Monero there, "
            "then lock and unlock to apply.\n\nFor Monero set:\n  monero.enabled = true\n"
            "  monero.wallet_rpc_path = path to monero-wallet-rpc from getmonero.org\n"
            "  monero.daemon = a node you trust, e.g. your own node:18081")
        try:
            if hasattr(os, "startfile"):
                os.startfile(config_path())  # nosec B606 - opens our own config file in the default editor
        except OSError:
            pass


class SendDialog(tk.Toplevel):
    def __init__(self, app: App, chain: Chain, asset: Asset):
        super().__init__(app)
        self.app, self.chain, self.asset = app, chain, asset
        self.title(f"Send {asset.symbol} on {chain.name}")
        self.transient(app)
        self.grab_set()
        f = ttk.Frame(self, padding=PAD)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Send {asset.symbol} on {chain.name}", style="Big.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="To address").grid(row=1, column=0, sticky="w", pady=6)
        self.to = ttk.Entry(f, width=70, font=MONO)
        self.to.grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Label(f, text=f"Amount ({asset.symbol})").grid(row=2, column=0, sticky="w")
        self.amount = ttk.Entry(f, width=24)
        self.amount.grid(row=2, column=1, sticky="w")
        self.max = tk.BooleanVar(value=False)
        if chain.supports_max and asset.contract is None:
            ttk.Checkbutton(f, text="Send entire balance", variable=self.max).grid(row=2, column=2, sticky="w")
        ttk.Label(f, text=f"Only send to a {chain.name} address. Sending to another network loses the funds.",
                  style="Warn.TLabel").grid(row=3, column=0, columnspan=3, sticky="w", pady=6)
        self.status = ttk.Label(f, text="")
        self.status.grid(row=4, column=0, columnspan=3, sticky="w")
        self.btn = ttk.Button(f, text="Review", command=self.review)
        self.btn.grid(row=5, column=2, sticky="e", pady=8)
        ttk.Button(f, text="Cancel", command=self.destroy).grid(row=5, column=1, sticky="e")
        self.to.focus_set()

    def review(self):
        to = self.to.get().strip()
        try:
            amount = None if self.max.get() else to_base_units(self.amount.get(), self.asset.decimals)
        except ValueError as e:
            err_box(e)
            return
        self.btn.state(["disabled"])
        self.status.config(text="Building and signing transaction…")

        def ok(ptx):
            self.status.config(text="")
            self.btn.state(["!disabled"])
            ConfirmDialog(self.app, self.chain, ptx, self)

        def bad(e):
            self.status.config(text="")
            self.btn.state(["!disabled"])
            err_box(e)

        self.app.run_bg(lambda: self.chain.prepare_send(self.asset, to, amount), ok, bad)


class ConfirmDialog(tk.Toplevel):
    def __init__(self, app: App, chain: Chain, ptx, parent: tk.Toplevel):
        super().__init__(app)
        self.title("Confirm transaction")
        self.transient(app)
        self.grab_set()
        f = ttk.Frame(self, padding=PAD)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Check every detail. Transactions cannot be reversed.", style="Warn.TLabel").pack(anchor="w")
        t = tk.Text(f, width=100, height=10, font=MONO, wrap="none")
        t.insert("1.0", ptx.summary())
        t.config(state="disabled")
        t.pack(fill="both", pady=8)
        bar = ttk.Frame(f)
        bar.pack(anchor="e")
        btn = ttk.Button(bar, text="Sign & send")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        btn.pack(side="left")

        def sent(txid):
            self.destroy()
            parent.destroy()
            if messagebox.askyesno("Sent", f"Broadcast.\n\nTransaction: {txid}\n\nOpen in block explorer?"):
                open_url(chain.tx_url(txid))

        def bad(e):
            btn.state(["!disabled"])
            err_box(e)

        def go():
            btn.state(["disabled"])
            app.run_bg(ptx.broadcast, sent, bad)

        btn.config(command=go)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
