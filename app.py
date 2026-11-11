import os, re, hmac, hashlib, secrets, datetime as dt
import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Date, Text, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

st.set_page_config(page_title="Quản lý dạy lái xe", page_icon="🚗", layout="centered")
Base = declarative_base()
HANG = ["B (số tự động)", "B (số sàn)", "C1", "A1", "A2", "D", "E"]


def cfg(key, default=None):
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


def vnd(n):
    return f"{int(n or 0):,}".replace(",", ".") + "đ"


def dmy(d):
    return d.strftime("%d/%m/%Y")


def hp(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 150000).hex()


def vp(pw, stored):
    return hmac.compare_digest(hp(pw, stored.split("$")[0]), stored)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(200), unique=True, nullable=False)
    name = Column(String(120), nullable=False)
    phone = Column(String(30), default="")
    pw = Column(String(300), nullable=False)
    role = Column(String(10), default="teacher")      # admin | teacher
    status = Column(String(10), default="pending")    # pending | active | locked
    students = relationship("Student", cascade="all, delete-orphan")


class Student(Base):
    __tablename__ = "students"
    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    phone = Column(String(30), default="")
    hang = Column(String(30), default="B")
    fee = Column(Integer, default=0)
    payments = relationship("Payment", cascade="all, delete-orphan", order_by="Payment.date.desc()")
    lessons = relationship("Lesson", cascade="all, delete-orphan", order_by="Lesson.date, Lesson.time")
    evals = relationship("Eval", cascade="all, delete-orphan", order_by="Eval.id.desc()")

    @property
    def paid(self):
        return sum(p.amount for p in self.payments)

    @property
    def rest(self):
        return max((self.fee or 0) - self.paid, 0)


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    amount = Column(Integer, nullable=False)
    date = Column(Date, default=dt.date.today)
    note = Column(String(200), default="")


class Lesson(Base):
    __tablename__ = "lessons"
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    date = Column(Date, nullable=False)
    time = Column(String(5), default="08:00")
    place = Column(String(200), default="")
    status = Column(String(10), default="plan")       # plan | done
    score = Column(Integer)
    note = Column(String(500), default="")


class Eval(Base):
    __tablename__ = "evals"
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    date = Column(Date, default=dt.date.today)
    text = Column(Text, nullable=False)


@st.cache_resource
def get_factory():
    url = cfg("DATABASE_URL", "sqlite:///data.db")
    url = re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql://", url.strip())  # dùng psycopg2
    eng = create_engine(url, pool_pre_ping=True, pool_recycle=300)
    Base.metadata.create_all(eng)
    f = sessionmaker(eng, expire_on_commit=False)
    em, pw = cfg("ADMIN_EMAIL"), cfg("ADMIN_PASSWORD")
    if em and pw:
        em = str(em).strip().lower()
        with f() as s:
            a = s.query(User).filter_by(email=em).first()
            if not a:
                s.add(User(email=em, name="Admin", pw=hp(str(pw)), role="admin", status="active"))
            else:  # luôn đồng bộ với Secrets
                a.role, a.status, a.pw = "admin", "active", hp(str(pw))
            s.commit()
    return f


def ai_eval(s):
    done = [l for l in s.lessons if l.status == "done"]
    if not done:
        return None, "Cần ít nhất 1 buổi đã hoàn thành."
    key = cfg("ANTHROPIC_API_KEY")
    if not key:
        return None, "Chưa cấu hình ANTHROPIC_API_KEY trong Secrets."
    log = "\n".join(f"{dmy(l.date)}: điểm {l.score}/10 - {l.note or 'không có nhận xét'}" for l in done)
    prompt = (f"Bạn là giáo viên dạy lái xe giàu kinh nghiệm. Học viên: {s.name}, hạng {s.hang}.\n"
              f"Nhật ký các buổi học:\n{log}\n\nĐánh giá bằng tiếng Việt, tối đa 150 từ, gồm: "
              "1) Tiến bộ; 2) Điểm mạnh; 3) Điểm cần cải thiện; 4) Gợi ý buổi tới; "
              "5) Mức sẵn sàng thi sát hạch. Dùng văn bản thường, không markdown.")
    try:
        import anthropic
        m = anthropic.Anthropic(api_key=key).messages.create(
            model=cfg("AI_MODEL", "claude-sonnet-5-5"), max_tokens=600,
            messages=[{"role": "user", "content": prompt}])
        return m.content[0].text, None
    except Exception as e:
        return None, f"Lỗi AI: {e}"


# ---------------- Đăng nhập / đăng ký ----------------
def auth(S):
    st.title("🚗 Quản lý dạy lái xe")
    if not cfg("ADMIN_EMAIL") or not cfg("ADMIN_PASSWORD"):
        st.warning("Chưa đọc được ADMIN_EMAIL / ADMIN_PASSWORD trong Secrets.")
    t1, t2 = st.tabs(["Đăng nhập", "Đăng ký giáo viên"])
    with t1, st.form("login"):
        e = st.text_input("Email")
        p = st.text_input("Mật khẩu", type="password")
        if st.form_submit_button("Đăng nhập", type="primary"):
            u = S.query(User).filter_by(email=e.strip().lower()).first()
            if u and vp(p, u.pw):
                st.session_state["uid"] = u.id
                st.rerun()
            st.error("Sai email hoặc mật khẩu.")
    with t2, st.form("reg"):
        n = st.text_input("Họ tên")
        ph = st.text_input("Số điện thoại")
        e = st.text_input("Email ", key="re")
        p = st.text_input("Mật khẩu (tối thiểu 6 ký tự)", type="password", key="rp")
        if st.form_submit_button("Gửi đăng ký"):
            if not n.strip() or "@" not in e or len(p) < 6:
                st.error("Vui lòng nhập đủ họ tên, email hợp lệ và mật khẩu ≥ 6 ký tự.")
            elif S.query(User).filter_by(email=e.strip().lower()).first():
                st.error("Email đã được đăng ký.")
            else:
                S.add(User(email=e.strip().lower(), name=n.strip(), phone=ph.strip(), pw=hp(p)))
                S.commit()
                st.success("Đăng ký thành công. Hãy đăng nhập và chờ admin duyệt.")


# ---------------- Giáo viên ----------------
def overview(S, u):
    students = S.query(Student).filter_by(teacher_id=u.id).order_by(Student.name).all()
    today = dt.date.today()
    month = sum(p.amount for s in students for p in s.payments
                if (p.date.year, p.date.month) == (today.year, today.month))
    a, b, c = st.columns(3)
    a.metric("Tổng học phí", vnd(sum(s.fee or 0 for s in students)))
    b.metric("Đã thu", vnd(sum(s.paid for s in students)))
    c.metric("Thu tháng này", vnd(month))
    st.subheader("Lịch sắp tới")
    up = sorted([(l, s) for s in students for l in s.lessons if l.status == "plan" and l.date >= today],
                key=lambda x: (x[0].date, x[0].time))
    if up:
        st.dataframe([{"Ngày": dmy(l.date), "Giờ": l.time, "Học viên": s.name, "Địa điểm": l.place}
                      for l, s in up], hide_index=True, use_container_width=True)
    else:
        st.caption("Chưa có lịch.")
    st.subheader("Học phí còn nợ")
    debts = [{"Học viên": s.name, "Còn lại": vnd(s.rest)} for s in students if s.rest > 0]
    if debts:
        st.dataframe(debts, hide_index=True, use_container_width=True)
    else:
        st.caption("Không có khoản nợ.")


def students_page(S, u):
    students = S.query(Student).filter_by(teacher_id=u.id).order_by(Student.name).all()
    with st.expander("➕ Thêm học viên", expanded=not students):
        with st.form("addst", clear_on_submit=True):
            n = st.text_input("Họ tên")
            ph = st.text_input("Điện thoại")
            h = st.selectbox("Hạng", HANG)
            f = st.number_input("Học phí (đ)", min_value=0, step=100000)
            if st.form_submit_button("Lưu học viên", type="primary") and n.strip():
                s = Student(teacher_id=u.id, name=n.strip(), phone=ph, hang=h, fee=int(f))
                S.add(s)
                S.flush()
                st.session_state["sel"] = s.id
                S.commit()
                st.rerun()
    if not students:
        return
    ids = [s.id for s in students]
    if st.session_state.get("sel") not in ids:
        st.session_state["sel"] = ids[0]
    sid = st.selectbox("Chọn học viên", ids, key="sel",
                       format_func=lambda i: next(s.name for s in students if s.id == i))
    s = next(x for x in students if x.id == sid)
    st.header(s.name)
    st.caption(f"{s.hang} · {s.phone}")
    a, b, c = st.columns(3)
    a.metric("Học phí", vnd(s.fee))
    b.metric("Đã đóng", vnd(s.paid))
    c.metric("Còn lại", vnd(s.rest))
    done = [l for l in s.lessons if l.status == "done"]
    scs = [l.score for l in done if l.score is not None]
    a, b, c = st.columns(3)
    a.metric("Đã học", len(done))
    b.metric("Sắp tới", len(s.lessons) - len(done))
    c.metric("Điểm TB", round(sum(scs) / len(scs), 1) if scs else "–")

    with st.form(f"fee{s.id}"):
        f = st.number_input("Đặt học phí (đ)", min_value=0, step=100000, value=int(s.fee or 0))
        if st.form_submit_button("Cập nhật học phí"):
            s.fee = int(f)
            S.commit()
            st.rerun()

    st.subheader("Thanh toán")
    for p in s.payments:
        x, y = st.columns([5, 1])
        x.write(f"{dmy(p.date)} · **{vnd(p.amount)}** {p.note or ''}")
        if y.button("Xóa", key=f"dp{p.id}"):
            S.delete(p)
            S.commit()
            st.rerun()
    with st.form(f"pay{s.id}", clear_on_submit=True):
        amt = st.number_input("Số tiền (đ)", min_value=0, step=100000)
        note = st.text_input("Ghi chú")
        if st.form_submit_button("Thu phí", type="primary") and amt > 0:
            S.add(Payment(student_id=s.id, amount=int(amt), note=note))
            S.commit()
            st.rerun()

    st.subheader("Lịch học")
    for l in s.lessons:
        head = f"{dmy(l.date)} {l.time} · {l.place or ''}"
        if l.status == "done":
            st.write(f"✅ {head} — điểm **{l.score}/10** {l.note or ''}")
            continue
        with st.expander(f"🕒 {head}"):
            with st.form(f"dn{l.id}"):
                sc = st.slider("Điểm", 0, 10, 8)
                nt = st.text_input("Nhận xét (lỗi gặp phải, tiến bộ…)")
                if st.form_submit_button("Hoàn thành buổi học", type="primary"):
                    l.status, l.score, l.note = "done", sc, nt
                    S.commit()
                    st.rerun()
            if st.button("Xóa lịch", key=f"dl{l.id}"):
                S.delete(l)
                S.commit()
                st.rerun()
    with st.form(f"les{s.id}", clear_on_submit=True):
        d = st.date_input("Ngày", value=dt.date.today(), format="DD/MM/YYYY")
        t = st.time_input("Giờ", value=dt.time(8, 0), step=900)
        pl = st.text_input("Địa điểm / sân tập")
        if st.form_submit_button("Thêm lịch", type="primary"):
            S.add(Lesson(student_id=s.id, date=d, time=t.strftime("%H:%M"), place=pl))
            S.commit()
            st.rerun()

    st.subheader("Đánh giá bằng AI")
    if st.button("✨ Tạo đánh giá AI", type="primary"):
        with st.spinner("AI đang phân tích…"):
            text, err = ai_eval(s)
        if err:
            st.error(err)
        else:
            S.add(Eval(student_id=s.id, text=text))
            S.commit()
            st.rerun()
    for e in s.evals:
        st.info(f"**{dmy(e.date)}**\n\n{e.text}")

    with st.expander("Xóa học viên"):
        if st.checkbox("Tôi hiểu: xóa toàn bộ dữ liệu của học viên này", key=f"cf{s.id}") \
                and st.button("Xóa học viên", key=f"ds{s.id}"):
            S.delete(s)
            S.commit()
            st.rerun()


# ---------------- Admin ----------------
def admin_page(S):
    teachers = S.query(User).filter_by(role="teacher").order_by(User.status.desc(), User.name).all()
    st.subheader(f"Giáo viên ({len(teachers)})")
    if not teachers:
        st.caption("Chưa có giáo viên đăng ký.")
    label = {"pending": "⏳ Chờ duyệt", "active": "✅ Hoạt động", "locked": "🔒 Đã khóa"}
    for t in teachers:
        fee = sum(s.fee or 0 for s in t.students)
        paid = sum(s.paid for s in t.students)
        x, y = st.columns([4, 1])
        x.markdown(f"**{t.name}** · {t.email} · {t.phone}  \n{label[t.status]} · {len(t.students)} học viên · "
                   f"Đã thu {vnd(paid)} / {vnd(fee)}")
        txt, new = {"active": ("Khóa", "locked"), "pending": ("Duyệt", "active"),
                    "locked": ("Mở khóa", "active")}[t.status]
        if y.button(txt, key=f"ad{t.id}"):
            t.status = new
            S.commit()
            st.rerun()
        st.divider()


def main():
    S = get_factory()()
    try:
        u = S.get(User, st.session_state["uid"]) if "uid" in st.session_state else None
        if not u:
            return auth(S)
        with st.sidebar:
            st.write(f"👤 **{u.name}**")
            st.caption("Admin" if u.role == "admin" else "Giáo viên")
            pages = (["Quản trị"] if u.role == "admin" else []) + (["Tổng quan", "Học viên"] if u.status == "active" else [])
            page = st.radio("Menu", pages, label_visibility="collapsed") if pages else None
            if st.button("Đăng xuất"):
                st.session_state.clear()
                st.rerun()
        st.title("🚗 Quản lý dạy lái xe")
        if u.status == "pending":
            st.info("Quản trị viên sẽ duyệt tài khoản giáo viên của bạn. Vui lòng quay lại sau.")
        elif u.status == "locked":
            st.error("Tài khoản đã bị khóa. Vui lòng liên hệ quản trị viên.")
        elif page == "Quản trị":
            admin_page(S)
        elif page == "Tổng quan":
            overview(S, u)
        else:
            students_page(S, u)
    finally:
        S.close()


main()
