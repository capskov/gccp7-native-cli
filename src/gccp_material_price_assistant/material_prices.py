"""Standalone SQLite material quotation browser."""
from contextlib import closing
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sqlite3
import sys
from threading import Thread
import tkinter as tk
from tkinter import filedialog, font, messagebox, simpledialog, ttk

from gccp_material_price_assistant.gccp_link import GccpLink
from gccp_material_price_assistant.quote_matching import keyword_filter, material_type, rank_quotes

TITLE = "广联达外挂材料价格助手"
SETTINGS = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MaterialPrices" / "settings.json"
SEARCH_COLUMNS = (
    "q.material", "q.model", "q.specification", "q.brand", "q.supplier", "q.unit",
    "q.region", "q.quote_month", "q.remark", "CAST(q.dn AS TEXT)",
    "'DN' || q.dn", "category.category", "c.source",
)
COLUMNS = (
    ("material", "材料名称", 125),
    ("specification", "规格型号", 275),
    ("unit", "单位", 48),
    ("ex_tax_cents", "除税单价", 95),
    ("incl_tax_cents", "含税单价", 95),
    ("quote_month", "报价日期", 95),
    ("brand", "品牌", 105),
    ("source", "来源", 150),
)


def read_settings():
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(read_settings() | changes, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def find_database():
    saved = read_settings().get("database")
    if isinstance(saved, str) and Path(saved).is_file():
        return Path(saved)
    start = Path(sys.executable if getattr(sys, "frozen", False) else __file__).parent
    for folder in (start, *start.parents[:3]):
        files = sorted(folder.glob("data/材料市场价-*.sqlite"), reverse=True)
        if not files:
            files = sorted(folder.glob("*项目概算编制/市场价库/*/*.sqlite"), reverse=True)
        if files:
            return files[0]
    return None


def search_database(path, keywords):
    tokens = keywords.split()
    match = "(" + " OR ".join(
        f"instr(lower(coalesce({column}, '')), lower(?)) > 0" for column in SEARCH_COLUMNS
    ) + ")"
    filters = " AND ".join(match for _ in tokens) or "1"
    params = [token for token in tokens for _ in SEARCH_COLUMNS]
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return [dict(row) for row in db.execute(
            f"""SELECT q.*, c.source, c.capture_date, category.category
                FROM quote q
                JOIN capture c ON c.id = q.capture_id
                LEFT JOIN quote_category category ON category.capture_id = q.capture_id
                WHERE {filters}
                ORDER BY q.quote_month DESC, q.capture_id, q.rank""",
            params,
        )]


def query_quotes(path, keywords, reference="", filtered=False):
    if filtered and material_type(keywords):
        all_rows = search_database(path, "")
        matches = rank_quotes(all_rows, keywords, reference, core_only=True)
        if matches:
            return matches, False
        return rank_quotes(all_rows, keywords, reference), True

    matches = search_database(path, keywords)
    if matches or not keywords.strip():
        return matches, False
    return rank_quotes(search_database(path, ""), keywords, reference), True


def display_value(row, column):
    value = row.get(column)
    if column.endswith("_cents") and value is not None:
        price = Decimal(str(value)) / 100
        precision = max(2, -price.normalize().as_tuple().exponent)
        return f"{price:,.{precision}f}"
    return value if value not in (None, "") else "—"


class App:
    def __init__(self, root):
        self.root = root
        self.path = None
        self.link = GccpLink()
        self.selection = None
        self.sending = False
        self.loading_link = False
        self.rows = {}
        self.keyword = tk.StringVar()
        self.filter_keywords = tk.BooleanVar(value=True)
        self.result_label = tk.StringVar()
        self.link_label = tk.StringVar(value="未连接广联达")
        self.pinned = tk.BooleanVar(value=False)
        self.zoom = 9
        saved_widths = read_settings().get("column_widths")
        self.manual_widths = {
            key: width for key, width in (saved_widths.items() if isinstance(saved_widths, dict) else ())
            if key in {column[0] for column in COLUMNS} and type(width) is int and width >= 35
        }
        self.dragged_column = None
        self.sort_key = None
        self.sort_descending = False
        root.title(f"{TITLE}  v1.3")
        root.geometry("900x415")
        root.minsize(405, 260)
        root.configure(bg="#f1f4f8")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        path = find_database()
        if path:
            self.open_database(path)
        self.root.after(90, self.poll_link)

    def build(self):
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")
        self.ui_font = font.Font(family="Microsoft YaHei UI", size=self.zoom)
        self.heading_font = font.Font(family="Microsoft YaHei UI", size=self.zoom, weight="bold")
        self.style.configure(".", font=self.ui_font, background="#f1f4f8", foreground="#17283a")
        self.style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff",
                             borderwidth=0, font=self.ui_font)
        self.style.configure("Treeview.Heading", background="#e3eaf2", font=self.heading_font, padding=2)
        self.style.map("Treeview", background=[("selected", "#245d91")], foreground=[("selected", "#ffffff")])
        self.style.configure("Primary.TButton", background="#20598b", foreground="white", padding=(6, 2))
        self.style.map("Primary.TButton", background=[("active", "#174974")])
        self.style.configure("TButton", padding=(5, 2))
        self.style.configure("Sub.TLabel", foreground="#4c6074")
        self.style.configure("TEntry", padding=2)
        self.change_zoom(0)

        shell = ttk.Frame(self.root, padding=(6, 5))
        shell.pack(fill="both", expand=True)
        toolbar = ttk.Frame(shell)
        toolbar.pack(fill="x", pady=(0, 4))
        self.bind_button = ttk.Button(toolbar, text="连接广联达", command=self.toggle_link)
        self.bind_button.pack(side="left")
        ttk.Button(toolbar, text="打开数据库", command=self.choose_database).pack(side="left", padx=4)
        ttk.Checkbutton(toolbar, text="图钉置顶", variable=self.pinned,
                        command=self.update_pin).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="+", width=2, command=lambda: self.change_zoom(1)).pack(side="right")
        ttk.Button(toolbar, text="−", width=2, command=lambda: self.change_zoom(-1)).pack(side="right", padx=2)
        ttk.Label(toolbar, text="字号", style="Sub.TLabel").pack(side="right", padx=3)

        search_bar = ttk.Frame(shell)
        search_bar.pack(fill="x")
        ttk.Label(search_bar, text="关键字").pack(side="left", padx=(0, 5))
        entry = ttk.Entry(search_bar, textvariable=self.keyword, font=self.ui_font, width=8)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: self.search())
        ttk.Button(search_bar, text="查询", style="Primary.TButton", command=self.search).pack(side="left", padx=4)
        ttk.Button(search_bar, text="全部", command=self.show_all).pack(side="left")
        ttk.Checkbutton(search_bar, text="提取关键词", variable=self.filter_keywords,
                        command=self.search_selection).pack(side="left", padx=(5, 0))
        entry.focus_set()
        info = ttk.Frame(shell)
        info.pack(fill="x", pady=(3, 2))
        ttk.Label(info, textvariable=self.result_label, style="Sub.TLabel").pack(side="left", padx=(0, 8))
        self.link_status = ttk.Label(info, textvariable=self.link_label, style="Sub.TLabel")
        self.link_status.pack(side="left", fill="x", expand=True)
        info.bind("<Configure>", lambda event: self.link_status.configure(wraplength=max(170, event.width - 100)))

        pane = ttk.Panedwindow(shell, orient="vertical")
        pane.pack(fill="both", expand=True)
        table_frame = ttk.Frame(pane)
        pane.add(table_frame, weight=5)
        columns = tuple(key for key, _, _ in COLUMNS)
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.table.tag_configure("even", background="#f3f7fb")
        for key, label, _ in COLUMNS:
            self.table.heading(key, text=label, command=lambda col=key: self.sort_table(col))
            self.table.column(key, width=90, minwidth=35, stretch=key == "specification",
                              anchor="e" if key.endswith("_cents") else "w")
        table_frame.bind("<Configure>", self.resize_columns)
        self.table.grid(row=0, column=0, sticky="nsew")
        for orient, view, options in (
            ("vertical", self.table.yview, {"row": 0, "column": 1, "sticky": "ns"}),
            ("horizontal", self.table.xview, {"row": 1, "column": 0, "sticky": "ew"}),
        ):
            bar = ttk.Scrollbar(table_frame, orient=orient, command=view)
            bar.grid(**options)
            self.table.configure(**{("y" if orient == "vertical" else "x") + "scrollcommand": bar.set})
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table.bind("<ButtonPress-1>", self.begin_column_resize, add="+")
        self.table.bind("<ButtonRelease-1>", self.end_column_resize, add="+")
        self.table.bind("<<TreeviewSelect>>", self.show_details)
        self.table.bind("<Double-1>", self.double_click)

        detail_frame = ttk.Frame(pane, padding=(0, 2, 0, 0))
        pane.add(detail_frame, weight=1)
        ttk.Label(detail_frame, text="报价详情", font=self.heading_font).pack(anchor="w", pady=(0, 2))
        detail_body = ttk.Frame(detail_frame)
        detail_body.pack(fill="both", expand=True)
        self.details = tk.Text(detail_body, height=3, wrap="word", font=self.ui_font, state="disabled",
                               bg="#ffffff", fg="#17283a", padx=5, pady=3, relief="flat")
        self.details.pack(side="left", fill="both", expand=True)
        detail_scroll = ttk.Scrollbar(detail_body, command=self.details.yview)
        detail_scroll.pack(side="right", fill="y")
        self.details.configure(yscrollcommand=detail_scroll.set)

    def update_pin(self):
        self.root.attributes("-topmost", self.pinned.get())

    def change_zoom(self, amount):
        self.zoom = max(7, min(14, self.zoom + amount))
        self.ui_font.configure(size=self.zoom)
        self.heading_font.configure(size=self.zoom)
        self.style.configure("Treeview", rowheight=self.ui_font.metrics("linespace") + 5)
        if hasattr(self, "table"):
            self.resize_columns()

    def resize_columns(self, event=None):
        width = event.width if event else self.table.master.winfo_width()
        if width < 5 or self.dragged_column is not None:
            return
        if width < 540:
            sizes = (80, 115, 31, 57, 57, 61, 0, 0)
        elif width < 740:
            sizes = (93, 146, 34, 69, 69, 72, 66, 0)
        else:
            sizes = (110, 185, 40, 78, 78, 78, 78, 110)
        scale = self.zoom / 9
        shown = []
        for (key, _, _), size in zip(COLUMNS, sizes):
            if size:
                shown.append(key)
                self.table.column(key, width=self.manual_widths.get(key, round(size * scale)),
                                  stretch=key == "specification" and key not in self.manual_widths)
        self.table.configure(displaycolumns=shown)

    def begin_column_resize(self, event):
        if self.table.identify_region(event.x, event.y) != "separator":
            return
        column = self.table.identify_column(event.x)
        if column and column != "#0":
            self.dragged_column = self.table.column(column, "id")

    def end_column_resize(self, _event):
        key = self.dragged_column
        if key is None:
            return
        self.dragged_column = None
        self.manual_widths[key] = self.table.column(key, "width")
        self.table.column(key, stretch=False)
        save_settings(column_widths=self.manual_widths)

    def sort_table(self, key):
        if self.sort_key == key:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_key, self.sort_descending = key, False
        self.apply_sort()

    def apply_sort(self):
        if self.sort_key is None:
            return
        key = self.sort_key
        ordered = sorted(self.rows, key=lambda item: (self.rows[item][key] is None, self.rows[item][key]),
                         reverse=self.sort_descending)
        self.table.set_children("", *ordered)
        for index, item in enumerate(ordered):
            self.table.item(item, tags=("even",) if index % 2 else ())
        for col, label, _ in COLUMNS:
            marker = (" ▼" if self.sort_descending else " ▲") if col == key else ""
            self.table.heading(col, text=label + marker)

    def choose_database(self):
        path = filedialog.askopenfilename(
            title="打开市场价库", filetypes=[("SQLite 数据库", "*.sqlite *.sqlite3 *.db"), ("所有文件", "*.*")],
            initialdir=self.path.parent if self.path else None,
        )
        if path:
            self.open_database(Path(path))

    def open_database(self, path):
        try:
            rows, approximate = query_quotes(path.resolve(), self.keyword.get())
        except (OSError, sqlite3.Error) as error:
            messagebox.showerror(TITLE, f"无法读取市场价库。\n{error}", parent=self.root)
            return
        self.path = path.resolve()
        self.fill_table(rows, approximate)
        save_settings(database=str(self.path))

    def show_all(self):
        self.keyword.set("")
        self.search()

    def search_selection(self):
        if self.selection is None:
            return
        target = self.selection["body"]["Materials"][0]
        name, spec = target["Name"], target.get("Spec", "")
        filtered = self.filter_keywords.get()
        self.keyword.set(keyword_filter(name, spec) if filtered else name)
        if self.path:
            self.search(reference=f"{name} {spec}", filtered=filtered)

    def search(self, reference="", filtered=False):
        if self.path is None:
            self.choose_database()
            return
        try:
            rows, approximate = query_quotes(self.path, self.keyword.get(), reference, filtered)
            if approximate or filtered:
                self.sort_key = None
                for key, label, _ in COLUMNS:
                    self.table.heading(key, text=label)
            self.fill_table(rows, approximate)
        except (OSError, sqlite3.Error) as error:
            messagebox.showerror(TITLE, f"查询失败。\n{error}", parent=self.root)

    def fill_table(self, rows, approximate=False):
        self.table.delete(*self.table.get_children())
        self.rows = {}
        for index, row in enumerate(rows):
            key = f"{row['capture_id']}:{row['rank']}"
            self.rows[key] = row
            self.table.insert("", "end", iid=key, values=[display_value(row, col) for col, _, _ in COLUMNS],
                              tags=("even",) if index % 2 else ())
        self.apply_sort()
        if approximate:
            self.result_label.set(f"无精确匹配 · {len(rows):,} 条近似报价" if rows else "未找到相关报价")
        else:
            self.result_label.set(f"{len(rows):,} 条报价")
        if rows:
            first = self.table.get_children()[0]
            self.table.selection_set(first)
            self.table.focus(first)
        self.show_details()

    def show_details(self, _event=None):
        selection = self.table.selection()
        row = self.rows.get(selection[0]) if selection else None
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        if row:
            fields = [
                ("材料", "material"), ("类别", "category"), ("规格参数", "specification"), ("单位", "unit"),
                ("报价日期", "quote_month"), ("除税单价", "ex_tax_cents"), ("含税单价", "incl_tax_cents"),
                ("税率", "tax_rate"), ("来源", "source"), ("采集日期", "capture_date"),
                ("品牌", "brand"), ("地区", "region"), ("供应商", "supplier"), ("备注", "remark"),
            ]
            lines = [f"{name}  {display_value(row, key)}" for name, key in fields]
            self.details.insert("end", "   ·   ".join(lines[:4]) + "\n" +
                                "   ·   ".join(lines[4:10]) + "\n" +
                                "   ·   ".join(lines[10:]).replace("\\n", "\n"))
        self.details.configure(state="disabled")

    def toggle_link(self):
        if self.link.session:
            self.link.close()
            self.set_disconnected()
            return
        self.loading_link = True
        self.bind_button.configure(state="disabled")
        self.link_label.set("正在连接广联达…")

        def connect():
            try:
                self.link.connect()
                self.link.events.put(("connected", None))
            except Exception as error:
                self.link.events.put(("connect_error", str(error)))

        Thread(target=connect, daemon=True).start()

    def set_disconnected(self):
        self.selection = None
        self.pinned.set(False)
        self.update_pin()
        self.bind_button.configure(text="连接广联达", state="normal")
        self.link_label.set("未连接广联达")

    def poll_link(self):
        while not self.link.events.empty():
            event, value = self.link.events.get()
            if event == "selection":
                self.selection = value
                if value:
                    target = value["body"]["Materials"][0]
                    self.link_label.set(f"广联达选中  ·  {target['Name']}  {target['Unit']}")
                    self.search_selection()
                else:
                    self.link_label.set("已连接 · 请点选一条人材机")
            elif event == "connected":
                self.loading_link = False
                self.bind_button.configure(text="断开广联达", state="normal")
                self.pinned.set(True)
                self.update_pin()
                if self.selection is None:
                    self.link_label.set("已连接 · 请在广联达点选一条人材机")
            elif event in ("detached", "connect_error"):
                self.loading_link = False
                self.link.close()
                self.set_disconnected()
                if value:
                    messagebox.showerror(TITLE, f"连接失败。\n{value}", parent=self.root)
            elif event in ("sent", "send_error"):
                self.sending = False
                self.bind_button.configure(state="normal")
                if event == "sent":
                    self.link_label.set(f"已发送报价 · {value} · 请在广联达点「应用修改」")
                else:
                    messagebox.showerror(TITLE, value, parent=self.root)
        self.root.after(90, self.poll_link)

    def double_click(self, event):
        row = self.rows.get(self.table.identify_row(event.y)) if self.table.identify_region(event.x, event.y) == "cell" else None
        if row is None:
            return
        if not self.link.session:
            return
        if self.sending:
            return
        selection = self.selection
        if not selection:
            messagebox.showinfo(TITLE, "请在广联达的人材机汇总里点选目标行。", parent=self.root)
            return
        target = selection["body"]["Materials"][0]
        coefficient = Decimal(1)
        if row["unit"].strip() != target["Unit"].strip():
            answer = simpledialog.askstring(
                "单位换算",
                f"报价单位  {row['unit']}\n广联达单位  {target['Unit']}\n\n"
                f"1 {target['Unit']} 相当于多少 {row['unit']}？留空按 1。",
                parent=self.root,
            )
            if answer is None:
                return
            try:
                coefficient = Decimal(answer.strip() or "1")
                if not coefficient.is_finite() or coefficient <= 0:
                    raise InvalidOperation
            except InvalidOperation:
                messagebox.showerror(TITLE, "请输入大于 0 的换算系数。", parent=self.root)
                return
        if self.selection is not selection:
            messagebox.showinfo(TITLE, "广联达选中行已变化，请重新双击报价。", parent=self.root)
            return

        self.sending = True
        self.bind_button.configure(state="disabled")

        def send():
            try:
                self.link.send_quote(selection, row, coefficient)
                self.link.events.put(("sent", target["Name"]))
            except Exception as error:
                self.link.events.put(("send_error", str(error)))

        Thread(target=send, daemon=True).start()

    def close(self):
        if self.loading_link or self.sending:
            return
        self.link.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()



if __name__ == "__main__":
    main()
