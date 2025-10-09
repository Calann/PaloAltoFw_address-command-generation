import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
import csv
import os
import re
import ipaddress

# ===================== 校验与规范化 =====================
NAME_PATTERN = re.compile(r'^[^\s/]+$')  # 不允许空格与 "/"

def validate_name(name, what="名称"):
    if not NAME_PATTERN.match(name):
        raise ValueError(f'{what} "{name}" 非法：不能包含空格或 "/"')

def validate_member_name(name):
    validate_name(name, what="成员名")

def normalize_ip_or_cidr(value):
    """
    合法性校验并规范化:
    - 允许: IPv4 / IPv6
    - 可以不带掩码: 自动补 /32 (IPv4) 或 /128 (IPv6)
    - 带掩码: 使用 ip_network(strict=False) 校验
    返回形如: "ip/prefixlen"1
    """
    v = value.strip()
    if not v:
        raise ValueError("IP 地址不能为空")
    try:
        if "/" in v:
            net = ipaddress.ip_network(v, strict=False)
            return f"{net.network_address}/{net.prefixlen}"
        else:
            ip = ipaddress.ip_address(v)
            default = 32 if ip.version == 4 else 128
            return f"{ip}/{default}"
    except Exception:
        raise ValueError(f'IP 地址不合法: "{value}"')

# ===================== 生成器核心逻辑 =====================
def gen_address_cmd(platform, name, cidr):
    if platform == "Firewall":
        return f"set address {name} ip-netmask {cidr}"
    else:  # Panorama
        return f"set shared address {name} ip-netmask {cidr}"

def gen_group_cmd(platform, group_name, members):
    cmds = []
    prefix = "set address-group" if platform == "Firewall" else "set shared address-group"
    for m in members:
        m = m.strip()
        if m:
            validate_member_name(m)
            cmds.append(f"{prefix} {group_name} static {m}")
    return cmds

# ===================== 文件读取助手（两步导入） =====================
def _read_lines_plaintext(path):
    with open(path, "r", encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f.readlines()]

def _read_csv_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            # 去 BOM 空列/空单元格
            cleaned = [c.strip() for c in row if c is not None]
            rows.append(cleaned)
    return rows

def read_names_from_file(path):
    """
    支持 .csv 或 .txt：
    - csv: 取第一列为名称
    - txt: 每行名称
    """
    ext = os.path.splitext(path)[1].lower()
    names = []
    if ext == ".csv":
        for row in _read_csv_rows(path):
            if not row:
                continue
            name = row[0].strip()
            if name:
                validate_name(name)
                names.append(name)
    else:
        for ln in _read_lines_plaintext(path):
            name = ln.strip()
            if name:
                validate_name(name)
                names.append(name)
    if not names:
        raise ValueError("未在文件中读取到任何有效名称")
    return names

def read_ips_from_file(path):
    """
    支持 .csv 或 .txt：
    - csv: 取第一列为 IP 或 IP/CIDR
    - txt: 每行一个 IP 或 IP/CIDR
    """
    ext = os.path.splitext(path)[1].lower()
    ips = []
    if ext == ".csv":
        for row in _read_csv_rows(path):
            if not row:
                continue
            raw = row[0].strip()
            if raw:
                ips.append(normalize_ip_or_cidr(raw))
    else:
        for ln in _read_lines_plaintext(path):
            raw = ln.strip()
            if raw:
                ips.append(normalize_ip_or_cidr(raw))
    if not ips:
        raise ValueError("未在文件中读取到任何有效 IP")
    return ips

def read_members_from_file(path):
    """
    支持 .csv 或 .txt：
    - csv: 每行第2列起为成员；可为空时报错
    - txt: 每行一个成员列表，用逗号分隔
    返回: List[List[str]]，每个子列表对应一行的成员
    """
    ext = os.path.splitext(path)[1].lower()
    lines_members = []
    if ext == ".csv":
        for row in _read_csv_rows(path):
            if not row:
                continue
            members = [c.strip() for c in row[1:] if c.strip()]
            if not members:
                # 允许空行跳过？为了与“行对齐”校验一致，这里保留空列表，由上层做一致性校验
                members = []
            # 校验成员名称
            for m in members:
                validate_member_name(m)
            lines_members.append(members)
    else:
        for ln in _read_lines_plaintext(path):
            if not ln.strip():
                lines_members.append([])  # 保留空行以便与组名对齐
                continue
            members = [c.strip() for c in ln.split(",") if c.strip()]
            for m in members:
                validate_member_name(m)
            lines_members.append(members)
    if not lines_members:
        raise ValueError("未在文件中读取到任何成员行")
    return lines_members

# ===================== 输出相关 =====================
def show_output(cmds):
    output_text.config(state="normal")
    output_text.delete("1.0", tk.END)
    if cmds:
        output_text.insert("1.0", "\n".join(cmds) + "\n")
    output_text.config(state="disabled")

def copy_to_clipboard():
    content = output_text.get("1.0", tk.END)
    if content.strip():
        root.clipboard_clear()
        root.clipboard_append(content)
        result_label.config(text="命令已复制到剪贴板")

def save_to_file():
    content = output_text.get("1.0", tk.END)
    if not content.strip():
        return
    path = filedialog.asksaveasfilename(
        title="保存命令到文件",
        defaultextension=".txt",
        filetypes=[("Text Files","*.txt"), ("All Files","*.*")]
    )
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        result_label.config(text=f"命令已保存: {path}")

# ===================== 手动输入（两步，带校验） =====================
def input_address_manual():
    # 第一步：名称列表
    top1 = tk.Toplevel(root)
    top1.title("输入地址对象名称（每行一个）")
    top1.geometry("420x260")
    top1.transient(root); top1.grab_set()

    tk.Label(top1, text="请输入地址对象名称（每行一个，不含空格和 /）：").pack(pady=6)
    frame1 = tk.Frame(top1); frame1.pack(fill="both", expand=True, padx=10)
    scroll1 = tk.Scrollbar(frame1); scroll1.pack(side="right", fill="y")
    name_box = tk.Text(frame1, height=10, width=48, yscrollcommand=scroll1.set)
    name_box.pack(side="left", fill="both", expand=True); scroll1.config(command=name_box.yview)

    def next_to_ip():
        try:
            names = [ln.strip() for ln in name_box.get("1.0", tk.END).splitlines() if ln.strip()]
            if not names: raise ValueError("名称不能为空")
            for n in names: validate_name(n)
        except Exception as e:
            messagebox.showerror("错误", str(e)); return
        top1.destroy()
        input_address_ips(names)

    ttk.Button(top1, text="下一步", command=next_to_ip).pack(pady=8)

def input_address_ips(names):
    top2 = tk.Toplevel(root)
    top2.title("输入地址/掩码（每行一个）")
    top2.geometry("420x260")
    top2.transient(root); top2.grab_set()

    tk.Label(top2, text="请输入 IP 或 IP/CIDR（每行一个；未写掩码将自动补 /32 或 /128）：").pack(pady=6)
    frame2 = tk.Frame(top2); frame2.pack(fill="both", expand=True, padx=10)
    scroll2 = tk.Scrollbar(frame2); scroll2.pack(side="right", fill="y")
    ip_box = tk.Text(frame2, height=10, width=48, yscrollcommand=scroll2.set)
    ip_box.pack(side="left", fill="both", expand=True); scroll2.config(command=ip_box.yview)

    def confirm_ips():
        try:
            ips_raw = [ln.strip() for ln in ip_box.get("1.0", tk.END).splitlines() if ln.strip()]
            if not ips_raw: raise ValueError("IP 地址不能为空")
            if len(ips_raw) != len(names):
                raise ValueError(f"数量不一致：名称 {len(names)} 行，IP {len(ips_raw)} 行")
            ips = [normalize_ip_or_cidr(v) for v in ips_raw]
        except Exception as e:
            messagebox.showerror("错误", str(e)); return

        platform = platform_var.get()
        cmds = [gen_address_cmd(platform, n, ip) for n, ip in zip(names, ips)]
        show_output(cmds)
        result_label.config(text="已从手动输入生成地址对象命令")
        top2.destroy()

    ttk.Button(top2, text="生成命令", command=confirm_ips).pack(pady=8)

def input_group_manual():
    # 第一步：组名
    top1 = tk.Toplevel(root)
    top1.title("输入地址组名称（每行一个）")
    top1.geometry("420x260")
    top1.transient(root); top1.grab_set()

    tk.Label(top1, text="请输入地址组名称（每行一个，不含空格和 /）：").pack(pady=6)
    frame1 = tk.Frame(top1); frame1.pack(fill="both", expand=True, padx=10)
    scroll1 = tk.Scrollbar(frame1); scroll1.pack(side="right", fill="y")
    group_box = tk.Text(frame1, height=10, width=48, yscrollcommand=scroll1.set)
    group_box.pack(side="left", fill="both", expand=True); scroll1.config(command=group_box.yview)

    def next_to_members():
        try:
            groups = [ln.strip() for ln in group_box.get("1.0", tk.END).splitlines() if ln.strip()]
            if not groups: raise ValueError("组名不能为空")
            for g in groups: validate_name(g, what="组名")
        except Exception as e:
            messagebox.showerror("错误", str(e)); return
        top1.destroy()
        input_group_members(groups)

    ttk.Button(top1, text="下一步", command=next_to_members).pack(pady=8)

def input_group_members(groups):
    top2 = tk.Toplevel(root)
    top2.title("输入组成员（每行对应一个组，成员用逗号分隔）")
    top2.geometry("520x280")
    top2.transient(root); top2.grab_set()

    tk.Label(top2, text="每行对应上一步的一个组；成员以逗号分隔，不含空格和 /").pack(pady=6)
    frame2 = tk.Frame(top2); frame2.pack(fill="both", expand=True, padx=10)
    scroll2 = tk.Scrollbar(frame2); scroll2.pack(side="right", fill="y")
    mem_box = tk.Text(frame2, height=10, width=60, yscrollcommand=scroll2.set)
    mem_box.pack(side="left", fill="both", expand=True); scroll2.config(command=mem_box.yview)

    def confirm_groups():
        try:
            raw_lines = [ln for ln in mem_box.get("1.0", tk.END).splitlines()]
            mem_lines = [ln.strip() for ln in raw_lines if ln.strip()]
            if not mem_lines:
                raise ValueError("成员行不能为空")
            if len(mem_lines) != len(groups):
                raise ValueError(f"数量不一致：组名 {len(groups)} 行，成员 {len(mem_lines)} 行")
            all_members = []
            for line in mem_lines:
                members = [m.strip() for m in line.split(",") if m.strip()]
                if not members:
                    raise ValueError("存在成员为空的行，请检查")
                for m in members:
                    validate_member_name(m)
                all_members.append(members)
        except Exception as e:
            messagebox.showerror("错误", str(e)); return

        platform = platform_var.get()
        cmds = []
        for g, members in zip(groups, all_members):
            cmds.extend(gen_group_cmd(platform, g, members))
        show_output(cmds)
        result_label.config(text="已从手动输入生成地址组命令")
        top2.destroy()

    ttk.Button(top2, text="生成命令", command=confirm_groups).pack(pady=8)

# ===================== 两步文件导入（带校验） =====================
def import_file_two_step():
    if current_mode_var.get() == "address":
        import_address_two_step()
    else:
        import_group_two_step()

def _ask_file(title):
    return filedialog.askopenfilename(
        title=title,
        filetypes=[("CSV or TXT", "*.csv *.txt"), ("All Files", "*.*")]
    )

def import_address_two_step():
    # 第一步：选择名称文件
    path_names = _ask_file("选择名称文件（csv: 第一列；txt: 每行一个）")
    if not path_names:
        return
    try:
        names = read_names_from_file(path_names)
    except Exception as e:
        messagebox.showerror("名称文件错误", str(e)); return

    # 第二步：选择 IP 文件
    path_ips = _ask_file("选择 IP 文件（csv: 第一列；txt: 每行一个）")
    if not path_ips:
        return
    try:
        ips_raw = read_ips_from_file(path_ips)
    except Exception as e:
        messagebox.showerror("IP 文件错误", str(e)); return

    if len(names) != len(ips_raw):
        messagebox.showerror("数量不一致", f"名称 {len(names)} 行，IP {len(ips_raw)} 行"); return

    platform = platform_var.get()
    cmds = [gen_address_cmd(platform, n, ip) for n, ip in zip(names, ips_raw)]
    show_output(cmds)
    result_label.config(text=f"已从文件生成地址对象命令：{os.path.basename(path_names)}, {os.path.basename(path_ips)}")

def import_group_two_step():
    # 第一步：选择组名文件
    path_groups = _ask_file("选择组名文件（csv: 第一列；txt: 每行一个）")
    if not path_groups:
        return
    try:
        groups = read_names_from_file(path_groups)  # 组名同样按“名称”规则读取+校验
    except Exception as e:
        messagebox.showerror("组名文件错误", str(e)); return

    # 第二步：选择成员文件
    path_members = _ask_file("选择成员文件（csv: 每行从第2列起；txt: 每行用逗号分隔）")
    if not path_members:
        return
    try:
        members_lines = read_members_from_file(path_members)
    except Exception as e:
        messagebox.showerror("成员文件错误", str(e)); return

    # 与组名行数一致校验
    # 注意：read_members_from_file 对空行会返回 [] 来保持行对齐
    # 这里将空行视为“无成员”，按业务需要禁止：
    if len(members_lines) != len(groups):
        messagebox.showerror("数量不一致", f"组名 {len(groups)} 行，成员 {len(members_lines)} 行"); return
    if any(len(members) == 0 for members in members_lines):
        messagebox.showerror("成员缺失", "存在成员为空的行，请补充成员后再导入"); return

    platform = platform_var.get()
    cmds = []
    for g, members in zip(groups, members_lines):
        # 成员名校验已在 read_members_from_file 做过，这里直接生成
        cmds.extend(gen_group_cmd(platform, g, members))
    show_output(cmds)
    result_label.config(text=f"已从文件生成地址组命令：{os.path.basename(path_groups)}, {os.path.basename(path_members)}")

# ===================== 菜单切换（原位） =====================
def clear_menu_frame():
    for w in menu_frame.winfo_children():
        w.destroy()

def show_level1():
    clear_menu_frame()
    ttk.Button(menu_frame, text="地址对象", width=20, command=show_addr_obj).pack(pady=6)
    ttk.Button(menu_frame, text="地址组对象", width=20, command=show_addr_group).pack(pady=6)

def show_addr_obj():
    current_mode_var.set("address")
    clear_menu_frame()
    #ttk.Button(menu_frame, text="从文件导入（两步）", width=22, command=import_file_two_step).pack(pady=6)
    ttk.Button(menu_frame, text="手动输入（两步）", width=22, command=input_address_manual).pack(pady=6)
    ttk.Button(menu_frame, text="返回上一级", width=22, command=show_level1).pack(pady=(16,6))

def show_addr_group():
    current_mode_var.set("group")
    clear_menu_frame()
    #ttk.Button(menu_frame, text="从文件导入（两步）", width=22, command=import_file_two_step).pack(pady=6)
    ttk.Button(menu_frame, text="手动输入（两步）", width=22, command=input_group_manual).pack(pady=6)
    ttk.Button(menu_frame, text="返回上一级", width=22, command=show_level1).pack(pady=(16,6))

# ===================== 主窗口与布局（固定宽度+居中） =====================
root = tk.Tk()
root.title("地址对象命令转换器")
root.geometry("860x600")

# 可选主题
try:
    root.call("source", "sun-valley.tcl")
    ttk.Style().theme_use("sun-valley-dark")
except Exception:
    pass

# 居中布局：三行 grid，中间放菜单区
root.grid_rowconfigure(0, weight=1)
root.grid_rowconfigure(2, weight=1)
root.grid_columnconfigure(0, weight=1)

# 顶部：平台选择
top_bar = tk.Frame(root)
top_bar.grid(row=0, column=0, sticky="n", pady=10)
tk.Label(top_bar, text="平台：").pack(side="left", padx=(0,6))
platform_var = tk.StringVar(value="Firewall")
platform_combo = ttk.Combobox(top_bar, textvariable=platform_var, state="readonly", width=16,
                              values=["Firewall", "Panorama"])
platform_combo.pack(side="left")

# 中部：菜单（一级/二级原位切换）
menu_frame = tk.Frame(root)
menu_frame.grid(row=1, column=0)
current_mode_var = tk.StringVar(value="address")  # address / group
show_level1()

# 下部：输出区 + 操作按钮
bottom = tk.Frame(root)
bottom.grid(row=2, column=0, sticky="n", pady=10)

# 输出文本框（只读）
output_frame = tk.Frame(bottom)
output_frame.pack(pady=(6, 0))
scrollbar_out = tk.Scrollbar(output_frame); scrollbar_out.pack(side="right", fill="y")
output_text = tk.Text(output_frame, height=16, width=104, yscrollcommand=scrollbar_out.set, state="disabled")
output_text.pack(side="left", fill="both", expand=True)
scrollbar_out.config(command=output_text.yview)

# 输出操作按钮
actions = tk.Frame(bottom); actions.pack(pady=8)
ttk.Button(actions, text="复制命令到剪贴板", command=copy_to_clipboard).pack(side="left", padx=8)
ttk.Button(actions, text="保存到文件", command=save_to_file).pack(side="left", padx=8)

# 状态标签
result_label = tk.Label(root, text="请选择对象类型与输入来源生成命令", fg="blue")
result_label.grid(row=2, column=0, sticky="s", pady=(0,10))

root.mainloop()
