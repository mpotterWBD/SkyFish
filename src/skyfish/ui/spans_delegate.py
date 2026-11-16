from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

# Item data role holding [(text, QColor), ...] for a cell drawn in several
# colours. Cells without it are drawn normally.
SPANS_ROLE = Qt.UserRole + 1


class SpansDelegate(QStyledItemDelegate):
    """Draws a cell's text as coloured runs (SPANS_ROLE), so a variable's
    declaration reads in the list just as it's coloured in the code. The
    cell's plain DisplayRole text should hold the same characters, so sizing,
    copying and searching still work."""

    def paint(self, painter, option, index) -> None:
        spans = index.data(SPANS_ROLE)
        if not spans:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""  # background, selection and focus only; the text is ours
        style = opt.widget.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
        # The same inset Qt leaves before ordinary item text.
        margin = style.pixelMetric(QStyle.PM_FocusFrameHMargin, None, opt.widget) + 1
        x = rect.left() + margin
        metrics = QFontMetrics(opt.font)
        painter.save()
        painter.setFont(opt.font)
        for text, colour in spans:
            if x >= rect.right():
                break
            painter.setPen(colour)
            painter.drawText(QRect(x, rect.top(), rect.right() - x, rect.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, text)
            x += metrics.horizontalAdvance(text)
        painter.restore()
