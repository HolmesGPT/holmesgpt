# based on https://github.com/kostyachum/python-markdown-plain-text/blob/main/markdown_plain_text/extention.py
# MIT licensed
from xml.etree.ElementTree import Comment, ElementTree, ProcessingInstruction

from markdown import Extension, Markdown  # type: ignore


def _serialize_plain_text(write, elem):
    tag = elem.tag
    text = elem.text
    if tag is Comment:
        pass
    elif tag is ProcessingInstruction:
        pass
    elif tag is None:
        if text:
            write(text)
        for e in elem:
            _serialize_plain_text(write, e)
    else:
        if text:
            if tag.lower() not in ["script", "style"]:
                write(text)
        for e in elem:
            _serialize_plain_text(write, e)

    if elem.tail:
        write(elem.tail)


def _write_plain_text(root):
    assert root is not None
    data = []
    write = data.append
    _serialize_plain_text(write, root)
    return "".join(data)


def to_plain_text(element):
    return _write_plain_text(ElementTree(element).getroot())


class PlainTextExtension(Extension):
    def extendMarkdown(self, md):
        md.serializer = to_plain_text
        md.stripTopLevelTags = False

        # Extention register actually runs before the format is set and it ends up rewriting serializer that we have just changed
        md.set_output_format = lambda x: x


def markdown_to_plain_text(text):
    md = Markdown(extensions=[PlainTextExtension()])
    return md.convert(text)
