# Импортируем модули
import os
import random
import re
import sqlite3
import sys
from datetime import datetime

from PyQt6 import QtWidgets, QtCore, QtGui

# Пути к файлам — рядом со скриптом, чтобы находились при любом запуске
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, "favicon.jpg")
BIBLE_DB = os.path.join(BASE_DIR, "RST77.SQLite3")
# Отдельная база истории: программа с анекдотами тоже пишет в history.db,
# но с другой структурой таблицы — в общей базе они бы конфликтовали
HISTORY_DB = os.path.join(BASE_DIR, "bible_history.db")

# Интервал смены стихов в секундах (1800 секунд = 30 минут)
INTERVAL = 1800

# Размер кнопок и размер значков на них
BUTTON_SIZE = 80
BUTTON_FONT_SIZE = 36


# Создаем класс для окна с библейскими стихами
class BibleWindow(QtWidgets.QWidget):
    def __init__(self, quotes, history_conn):
        super().__init__()

        self.quotes = quotes                # список всех стихов (text, short_name, chapter, verse)
        self.history_conn = history_conn    # открытое соединение с базой истории
        self.deck = []                      # «колода» стихов — чтобы не повторялись
        self.current_quote = None           # кортеж текущего стиха
        self.current_rowid = None           # id текущей записи в истории
        self.liked = False                  # поставлен ли лайк текущему стиху

        # Заголовок, размер и положение окна
        self.setWindowTitle("Цитата из Библии")
        self.setGeometry(825, 34, 620, 500)
        self.setStyleSheet("background-color: #272727;")

        # Создаем шрифт с заданным размером и жирностью для текста стихов
        font = QtGui.QFont()
        font.setPointSize(27)
        font.setBold(True)

        # Создаем метку для вывода стихов.
        # QLabel понимает HTML, поэтому теги <i>...</i> из базы
        # сами превращаются в курсив
        self.label = QtWidgets.QLabel()
        self.label.setFont(font)
        self.label.setWordWrap(True)
        self.label.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
        self.label.setContentsMargins(10, 10, 10, 10)
        self.label.setStyleSheet("color: #FFFFFF;")

        # Оборачиваем метку в область с прокруткой, чтобы длинные стихи
        # не обрезались снизу — их можно будет дочитать, прокрутив текст
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)                       # метка тянется на всю область
        self.scroll_area.setWidget(self.label)
        self.scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)  # без рамки вокруг текста
        # Горизонтальная прокрутка не нужна — текст переносится по словам
        self.scroll_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Стилизуем полосу прокрутки под тёмный фон окна
        self.scroll_area.setStyleSheet("""
            QScrollArea { background-color: #272727; }
            QScrollBar:vertical { background: #272727; width: 12px; }
            QScrollBar::handle:vertical { background: #3a3a3a; border-radius: 6px; min-height: 40px; }
            QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical { height: 0; }
            QScrollBar::sub-page:vertical, QScrollBar::add-page:vertical { background: none; }
        """)

        # Создаем шрифт для значков на кнопках — крупный, чтобы было видно
        button_font = QtGui.QFont()
        button_font.setPointSize(BUTTON_FONT_SIZE)

        # Создаем кнопку «новый стих» и кнопку лайка
        self.next_button = QtWidgets.QPushButton("🎲")
        self.next_button.setToolTip("Показать новый стих")

        self.like_button = QtWidgets.QPushButton("👍")
        self.like_button.setToolTip("Поставить / убрать лайк")

        for btn in (self.next_button, self.like_button):
            btn.setFont(button_font)
            btn.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
            btn.setStyleSheet("color: #FFFFFF; background-color: #3a3a3a; border-radius: 16px;")
            btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))

        self.next_button.clicked.connect(self.update_verse)
        self.like_button.clicked.connect(self.toggle_like)

        # Вертикальный компоновщик для кнопок — столбиком в правом верхнем углу
        buttons_layout = QtWidgets.QVBoxLayout()
        buttons_layout.addWidget(self.next_button)
        buttons_layout.addWidget(self.like_button)
        buttons_layout.addStretch(1)   # растяжка снизу прижимает кнопки к верху
        buttons_layout.setSpacing(12)  # расстояние между кнопками

        # Горизонтальный компоновщик всего окна: текст слева, кнопки справа
        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.addWidget(self.scroll_area, stretch=1)
        main_layout.addLayout(buttons_layout)

        # Таймер для автоматической смены стихов
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(INTERVAL * 1000)
        self.timer.timeout.connect(self.update_verse)
        self.timer.start()

        # Показываем первый стих сразу
        self.update_verse()

    # Функция очистки текста стиха от служебных тегов.
    # Теги <i> и </i> не трогаем — QLabel сам покажет их курсивом,
    # а разрывы страниц <pb/> убираем
    def clean_text(self, raw_text):
        return re.sub(r"<pb/>", "", raw_text)

    # Функция возвращает ссылку на место Писания в виде «Быт.1:1»
    def reference(self, quote):
        return f"{quote[1]}.{quote[2]}:{quote[3]}"

    # Функция собирает HTML для метки: текст стиха + серая ссылка поменьше,
    # при желании — сообщение о лайке
    def verse_html(self, quote, message=""):
        text = self.clean_text(quote[0])
        ref = self.reference(quote)
        html = (f"{text} <span style='color: #aaaaaa; font-size: 18px; "
                f"font-weight: normal;'>({ref})</span>")
        if message:
            html += f"<br><br>{message}"
        return f"<html>{html}</html>"

    # Функция берет случайный стих без повторов, пока все не покажутся
    def take_quote(self):
        if not self.deck:
            self.deck = random.sample(self.quotes, len(self.quotes))
            # Чтобы только что показанный стих не выпал первым в новом круге,
            # меняем его местами с началом новой колоды
            if len(self.deck) > 1 and self.deck[-1] == self.current_quote:
                self.deck[0], self.deck[-1] = self.deck[-1], self.deck[0]
        return self.deck.pop()

    # Функция обновления стиха
    def update_verse(self):
        self.current_quote = self.take_quote()
        text, short_name, chapter, verse = self.current_quote

        # Текущая дата и время в формате YYYY-MM-DD HH:MM:SS
        date_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Сохраняем стих в историю и запоминаем id записи
        cur = self.history_conn.execute(
            "INSERT INTO history (text, short_name, chapter, verse, date_time, liked) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (text, short_name, chapter, verse, date_time),
        )
        self.history_conn.commit()
        self.current_rowid = cur.lastrowid

        # Сбрасываем состояние кнопки лайка
        self.liked = False
        self.like_button.setText("👍")
        self.label.setText(self.verse_html(self.current_quote))
        # Прокручиваем текст к началу (если предыдущий стих прокрутили вниз)
        self.scroll_area.verticalScrollBar().setValue(0)

        # Перезапускаем таймер, чтобы 30 минут отсчитывались заново
        self.timer.start()

    # Функция для поставки и снятия лайка
    def toggle_like(self):
        self.liked = not self.liked

        # Обновляем поле liked только у текущей записи (по rowid) —
        # показы этого же стиха в прошлом не задеваются
        self.history_conn.execute(
            "UPDATE history SET liked = ? WHERE rowid = ?",
            (1 if self.liked else 0, self.current_rowid),
        )
        self.history_conn.commit()

        if self.liked:
            # ❤️ — сердечко с невидимым модификатором, чтобы рисовалось цветным
            self.like_button.setText("❤️")
            self.label.setText(self.verse_html(
                self.current_quote, "Вы поставили лайк этому стиху!"))
        else:
            self.like_button.setText("👍")
            self.label.setText(self.verse_html(self.current_quote))

    # Функция вызывается при закрытии окна
    def closeEvent(self, event):
        # Останавливаем таймер и закрываем базу истории
        self.timer.stop()
        self.history_conn.close()
        super().closeEvent(event)


def main():
    # Проверяем, что база с Библией существует
    if not os.path.exists(BIBLE_DB):
        sys.exit(f"База данных не найдена: {BIBLE_DB}")

    # Читаем все стихи и сразу закрываем базу
    conn = sqlite3.connect(BIBLE_DB)
    try:
        rows = conn.execute(
            "SELECT text, short_name, chapter, verse "
            "FROM verses JOIN books ON verses.book_number = books.book_number"
        ).fetchall()
    except sqlite3.OperationalError:
        sys.exit("В RST77.SQLite3 нет таблиц verses/books — проверьте файл базы")
    finally:
        conn.close()

    if not rows:
        sys.exit("Таблица verses пуста — показывать нечего")

    # Соединение с базой истории держим открытым всё время работы программы,
    # закроем его при закрытии окна (closeEvent)
    history_conn = sqlite3.connect(HISTORY_DB)
    history_conn.execute(
        "CREATE TABLE IF NOT EXISTS history "
        "(text TEXT, short_name TEXT, chapter INTEGER, verse INTEGER, "
        "date_time TEXT, liked INTEGER)"
    )
    history_conn.commit()

    # Создаем приложение и задаем иконку
    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(QtGui.QIcon(ICON_PATH))

    # Создаем и показываем окно
    window = BibleWindow(rows, history_conn)
    window.show()

    # Запускаем главный цикл приложения
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
