"""测试用最小合法 PDF 构造器（Helvetica 文本，纯 Python，无第三方依赖）。

xref 偏移在运行时精确计算，PyPDF2 可正常解析并抽取每页文本。
"""


def build_minimal_pdf(pages_text: list) -> bytes:
    """构造最小合法 PDF（每个元素一页文本）。"""
    n = len(pages_text)
    page_nums = [3 + i * 2 for i in range(n)]
    content_nums = [4 + i * 2 for i in range(n)]
    font_num = 3 + n * 2

    objects: list = [b""] * font_num

    def set_obj(num: int, body: bytes) -> None:
        objects[num - 1] = body

    kids = " ".join(f"{num} 0 R" for num in page_nums)
    set_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    set_obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for i, text in enumerate(pages_text):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        set_obj(page_nums[i], (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_num} 0 R >> >> /Contents {content_nums[i]} 0 R >>"
        ).encode())
        set_obj(content_nums[i],
                b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    set_obj(font_num, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode()
    return bytes(out)
