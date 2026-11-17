from collections.abc import Callable

import pytest

from redocx.ooxml import CT, NS, RT, Package

# Hand-written minimal WordprocessingML parts. They stand in for the real part
# writers until those exist, and exercise the package writer end to end.

STYLES_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{NS.W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1">
<w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
<w:pPr><w:keepNext/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr>
</w:style>
</w:styles>""".encode()

CORE_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="{NS.CP}" xmlns:dc="{NS.DC}">
<dc:title>Hello &amp; welcome</dc:title><dc:creator>redocx tests</dc:creator>
</cp:coreProperties>""".encode()


def document_xml(link_rel_id: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{NS.W}" xmlns:r="{NS.R}"><w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Hello, redocx</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">Written by the OPC package writer. See </w:t></w:r>
<w:hyperlink r:id="{link_rel_id}"><w:r><w:t>example.com</w:t></w:r></w:hyperlink></w:p>
<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>
<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"
 w:header="708" w:footer="708" w:gutter="0"/>
</w:sectPr></w:body></w:document>""".encode()


def build_hello_package() -> Package:
    pkg = Package()
    document = pkg.add_part("/word/document.xml", CT.WML_DOCUMENT_MAIN)
    styles = pkg.add_part("/word/styles.xml", CT.WML_STYLES, STYLES_XML)
    core = pkg.add_part("/docProps/core.xml", CT.CORE_PROPERTIES, CORE_XML)
    pkg.relate(document, RT.OFFICE_DOCUMENT)
    pkg.relate(core, RT.CORE_PROPERTIES)
    document.relate(styles, RT.STYLES)
    link_id = document.relate_external("https://example.com/?a=1&b=2", RT.HYPERLINK)
    document.data = document_xml(link_id)
    return pkg


@pytest.fixture
def hello_package() -> Callable[[], Package]:
    """Factory for a minimal but complete .docx package."""
    return build_hello_package
