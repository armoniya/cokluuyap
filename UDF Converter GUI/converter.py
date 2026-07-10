import os
import tempfile
import base64
import io
import zipfile
import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
import pypdf
from PIL import Image

def doc_to_docx(doc_path):
    """Converts a legacy .doc file to .docx using win32com.client (MS Word required on Windows)."""
    import win32com.client
    word = None
    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc_abs = os.path.abspath(doc_path)
        doc = word.Documents.Open(doc_abs)
        temp_dir = tempfile.gettempdir()
        docx_path = os.path.join(temp_dir, os.path.basename(doc_path) + "x")
        doc.SaveAs2(docx_path, FileFormat=16)  # 16 = wdFormatXMLDocument (.docx)
        doc.Close()
        return docx_path
    except Exception as e:
        raise RuntimeError(
            "Legacy .doc dosyası dönüştürülemedi. Bu özellik sisteminizde Microsoft Word "
            f"ve pywin32 kurulu olmasını gerektirir.\nHata detayı: {str(e)}"
        )
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass

def get_alignment(paragraph_element):
    alignment = paragraph_element.find('.//w:jc', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
    if alignment is not None:
        val = alignment.get(qn('w:val'))
        if val == 'center':
            return '1'
        elif val == 'right':
            return '2'
        elif val == 'both':
            return '3'
    return '0'  # Default to Left

def get_indent_attrs(paragraph_element):
    ind = paragraph_element.find('.//w:ind', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
    left = ind.get(qn('w:left')) if ind is not None else None
    right = ind.get(qn('w:right')) if ind is not None else None
    firstLine = ind.get(qn('w:firstLine')) if ind is not None else None
    
    # 20 twips = 1 pt. UDF expects pt values.
    indent_attrs = f'LeftIndent="{float(left) / 20 if left else 0.0}" RightIndent="{float(right) / 20 if right else 0.0}"'
    
    if firstLine:
        indent_attrs += f' FirstLineIndent="{float(firstLine) / 20}"'
    
    return indent_attrs

def get_tab_stops_xml(paragraph_element):
    tabs_element = paragraph_element.find('.//w:pPr/w:tabs', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
    tab_stops = []
    if tabs_element is not None:
        for tab in tabs_element.findall('.//w:tab', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}):
            pos_twips = tab.get(qn('w:pos'))
            align_val = tab.get(qn('w:val'))
            if pos_twips:
                pos_pt = float(pos_twips) / 20.0
                align_str = "LEFT"
                if align_val == 'right':
                    align_str = "RIGHT"
                elif align_val == 'center':
                    align_str = "CENTER"
                elif align_val == 'decimal':
                    align_str = "DECIMAL"
                tab_stops.append(f'<tabStop position="{pos_pt}" alignment="{align_str}" />')
    if tab_stops:
        return f'<tabStops>{"".join(tab_stops)}</tabStops>'
    return ""

def get_bullet_type(num_id):
    bullet_types = {
        "1": "BULLET_TYPE_ELLIPSE",
        "2": "BULLET_TYPE_RECTANGLE",
        "3": "BULLET_TYPE_RECTANGLE_D",
        "4": "BULLET_TYPE_ARROW",
        "5": "BULLET_TYPE_DIAMOND",
        "6": "BULLET_TYPE_TRIANGLE",
    }
    return bullet_types.get(num_id, "BULLET_TYPE_ELLIPSE")

def get_number_type(list_id):
    number_types = {
        "1": "NUMBER_TYPE_CHAR_SMALL_DOT",
        "2": "BULLET_TYPE_ARROW",
        "3": "NUMBER_TYPE_ROMAN_BIG_DOT",
        "4": "NUMBER_TYPE_CHAR_BIG_DOT",
        "5": "NUMBER_TYPE_CHAR_SMALL_PARANTHESE",
        "6": "NUMBER_TYPE_NUMBER_TRE",
        "7": "NUMBER_TYPE_ROMAN_SMALL_DOT",
        "8": "BULLET_TYPE_ELLIPSE",
        "9": "BULLET_TYPE_RECTANGLE",
        "10": "BULLET_TYPE_RECTANGLE_D",
        "11": "NUMBER_TYPE_NUMBER_PARANTHESE",
        "12": "BULLET_TYPE_DIAMOND",
        "13": "BULLET_TYPE_TRIANGLE",
    }
    return number_types.get(list_id, "NUMBER_TYPE_NUMBER_TRE")

def process_image(drawing, document):
    try:
        inline = drawing.find('.//wp:inline', namespaces={'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'})
        anchor = drawing.find('.//wp:anchor', namespaces={'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'})
        
        extent = None
        if inline is not None:
            extent = inline.find('.//wp:extent', namespaces={'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'})
        elif anchor is not None:
            extent = anchor.find('.//wp:extent', namespaces={'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'})
        
        if extent is not None:
            # cx and cy are in EMUs (English Metric Units). 1 pt = 12700 EMUs.
            # 1 inch = 914400 EMUs. UDF expects width/height in pt.
            width = int(extent.get('cx')) // 12700
            height = int(extent.get('cy')) // 12700
        else:
            width = height = 100

        blip = drawing.find('.//a:blip', namespaces={'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'})
        if blip is not None:
            rId = blip.get(qn('r:embed'))
            if rId in document.part.rels:
                image_part = document.part.rels[rId].target_part
                image_bytes = image_part.blob
                
                try:
                    with Image.open(io.BytesIO(image_bytes)) as img:
                        png_buffer = io.BytesIO()
                        img.save(png_buffer, format='PNG')
                        png_bytes = png_buffer.getvalue()
                        image_data = base64.b64encode(png_bytes).decode('utf-8')
                except Exception:
                    image_data = base64.b64encode(image_bytes).decode('utf-8')
                
                return image_data, width, height
    except Exception:
        pass
    return None, None, None

def process_paragraph(paragraph_element, document, current_offset):
    para_text = ""
    para_elements = []
    
    # Check for list items
    numPr = paragraph_element.find('.//w:numPr', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
    numbered = False
    list_id = ""
    list_level = ""
    number_type = ""
    
    if numPr is not None:
        ilvl = numPr.find('.//w:ilvl', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
        numId = numPr.find('.//w:numId', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
        if ilvl is not None and numId is not None:
            numbered = True
            list_id = numId.get(qn("w:val"))
            list_level = str(int(ilvl.get(qn("w:val"))) + 1)
            number_type = get_number_type(list_id)

    font_family = "Times New Roman"
    font_size = "12"

    for run in paragraph_element.findall('.//w:r', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}):
        # Process images
        drawing_elements = run.findall('.//w:drawing', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
        if drawing_elements:
            for drawing in drawing_elements:
                image_data, width, height = process_image(drawing, document)
                if image_data:
                    placeholder = '\uFFFC'  # Object Replacement Character
                    para_text += placeholder
                    para_elements.append(
                        f'<image imageData="{image_data}" '
                        f'startOffset="{current_offset}" length="1" width="{width}" height="{height}" />'
                    )
                    current_offset += 1
                else:
                    print("Failed to process image, skipping...")

        # Process text and tab characters
        text = run.findtext('.//w:t', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}) or ''
        has_tab = run.find('.//w:tab', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}) is not None
        
        if text or has_tab:
            font_family = run.findtext('.//w:rFonts[@w:ascii]', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}) or "Times New Roman"
            sz_val = run.findtext('.//w:sz', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}) or "24"
            font_size = str(int(sz_val) // 2)

            style_attrs = [f'family="{font_family}"', f'size="{font_size}"']
            
            is_bold = run.find('.//w:b', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}) is not None
            is_italic = run.find('.//w:i', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}) is not None
            
            style_attrs.append(f'bold="{"true" if is_bold else "false"}"')
            style_attrs.append(f'italic="{"true" if is_italic else "false"}"')
            
            # Color extraction from run properties
            color_element = run.find('.//w:color', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
            if color_element is not None:
                color_val = color_element.get(qn('w:val'))
                if color_val and color_val != 'auto':
                    try:
                        r = int(color_val[0:2], 16)
                        g = int(color_val[2:4], 16)
                        b = int(color_val[4:6], 16)
                        argb = (255 << 24) | (r << 16) | (g << 8) | b
                        signed_color = argb - 2**32 if argb >= 2**31 else argb
                        style_attrs.append(f'foreground="{signed_color}"')
                    except Exception:
                        pass
            
            style_attr_str = ' '.join(style_attrs)

            for child in run:
                if child.tag.endswith('}t'):  # Text
                    para_elements.append(f'<content startOffset="{current_offset}" length="{len(child.text)}" {style_attr_str} />')
                    para_text += child.text
                    current_offset += len(child.text)
                elif child.tag.endswith('}tab'):  # Tab
                    para_elements.append(f'<tab {style_attr_str} startOffset="{current_offset}" length="1" />')
                    para_text += '\t'
                    current_offset += 1

    # Apply spacing and line spacing
    spacing_element = paragraph_element.find('.//w:spacing', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
    space_before = None
    space_after = None
    line_spacing = None
    
    if spacing_element is not None:
        before = spacing_element.get(qn('w:before'))
        after = spacing_element.get(qn('w:after'))
        line = spacing_element.get(qn('w:line'))
        lineRule = spacing_element.get(qn('w:lineRule'))
        
        if before:
            space_before = float(before) / 20.0
        if after:
            space_after = float(after) / 20.0
        if line and lineRule:
            line_spacing = round(float(line) / 240.0, 2)
            
    # Default line spacing to 1.15 to match Word visual defaults in UYAP
    if line_spacing is None:
        line_spacing = 1.15

    # Determine alignment
    alignment = get_alignment(paragraph_element)
    # If the paragraph has actual tab characters, force left alignment (0) if it is justified (3)
    # to prevent the layout engine from stretching the tab gaps inconsistently.
    if '\t' in para_text and alignment == '3':
        alignment = '0'

    # Numaralandırma ve madde işareti özelliklerini paragraf elementine ekle
    paragraph_attrs = f'Alignment="{alignment}" {get_indent_attrs(paragraph_element)}'
    if space_before is not None:
        paragraph_attrs += f' SpaceBefore="{space_before}"'
    if space_after is not None:
        paragraph_attrs += f' SpaceAfter="{space_after}"'
    if line_spacing is not None:
        paragraph_attrs += f' LineSpacing="{line_spacing}"'
        
    if numbered:
        if number_type.startswith("NUMBER_TYPE_"):
            paragraph_attrs += f' Numbered="true" ListId="{list_id}" ListLevel="{list_level}" NumberType="{number_type}"'
        else:
            paragraph_attrs += f' Bulleted="true" ListId="{list_id}" ListLevel="{list_level}" BulletType="{number_type}"'

    # Get tabStops
    tab_stops_xml = get_tab_stops_xml(paragraph_element)

    # Add trailing newline character to match UDF standard
    para_text += "\n"
    para_elements.append(f'<content startOffset="{current_offset}" length="1" family="{font_family}" size="{font_size}" bold="false" italic="false" />')
    current_offset += 1

    paragraph_element_xml = f'<paragraph {paragraph_attrs}>{tab_stops_xml}{"".join(para_elements)}</paragraph>'
    return para_text, paragraph_element_xml, current_offset

def process_cell(cell_element, document, current_offset):
    cell_text = ""
    cell_elements = []
    paragraphs = cell_element.findall('.//w:p', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
    
    for paragraph in paragraphs:
        p_text, p_xml, current_offset = process_paragraph(paragraph, document, current_offset)
        cell_text += p_text
        cell_elements.append(p_xml)
        
    # If cell is completely empty, add a default empty content to prevent XML parser crash
    if not cell_text:
        cell_text = "\n"
        cell_elements.append(f'<paragraph Alignment="0"><content startOffset="{current_offset}" length="1" family="Times New Roman" size="12" bold="false" italic="false" /></paragraph>')
        current_offset += 1

    return cell_text, cell_elements, current_offset

def process_table(table_element, document, current_offset):
    table_text = ""
    rows = []
    grid_cols = table_element.findall('.//w:gridCol', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
    column_count = len(grid_cols)
    
    # Calculate column widths
    total_width = sum(int(col.get(qn('w:w'), '0')) for col in grid_cols)
    if total_width == 0:
        total_width = 1
    column_widths = [int(col.get(qn('w:w'), '0')) for col in grid_cols]
    column_spans = ",".join([str(max(1, int((width / total_width) * 300))) for width in column_widths])

    # Check table borders
    tblBorders = table_element.find('.//w:tblBorders', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})
    border_type = "borderCell"  # Default to visible borders
    if tblBorders is not None:
        border_elements = ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']
        all_borders_none = all(
            tblBorders.find(f'.//w:{border}', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}) is None or
            tblBorders.find(f'.//w:{border}', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}).get(qn('w:val')) in ['none', 'nil', '0']
            for border in border_elements
        )
        if all_borders_none:
            border_type = "borderNone"

    for row_index, row in enumerate(table_element.findall('.//w:tr', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'})):
        cells = []
        for cell in row.findall('.//w:tc', namespaces={'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}):
            cell_text, cell_elements, current_offset = process_cell(cell, document, current_offset)
            cells.append(f'<cell>{"".join(cell_elements)}</cell>')
            table_text += cell_text
            
        rows.append(f'<row rowName="row{row_index + 1}" rowType="dataRow">{"".join(cells)}</row>')

    table_xml = f'<table tableName="Sabit" columnCount="{column_count}" columnSpans="{column_spans}" border="{border_type}">{"".join(rows)}</table>'
    return table_text, table_xml, current_offset

def docx_to_udf_xml(docx_path):
    """Converts a .docx file to UDF content.xml raw bytes preserving rich formatting, tables, and lists."""
    doc = docx.Document(docx_path)
    
    content_list = []
    elements_list = []
    current_offset = 0
    
    # Iterate block level elements in document order
    for element in doc.element.body:
        if element.tag.endswith('p'):  # Paragraph
            para_text, para_xml, current_offset = process_paragraph(element, doc, current_offset)
            elements_list.append(para_xml)
            content_list.append(para_text)
        elif element.tag.endswith('tbl'):  # Table
            table_text, table_xml, current_offset = process_table(element, doc, current_offset)
            elements_list.append(table_xml)
            content_list.append(table_text)
            
    # Fallback to ensure we have at least one element
    if not content_list:
        content_list.append("\n")
        elements_list.append(f'<paragraph Alignment="0"><content startOffset="0" length="1" family="Times New Roman" size="12" bold="false" italic="false" /></paragraph>')

    full_text = "".join(content_list)
    elements_xml = "\n".join(elements_list)
    
    # Extract margins from document if available
    left_margin = 42.525
    right_margin = 42.525
    top_margin = 42.525
    bottom_margin = 42.525
    try:
        if doc.sections:
            section = doc.sections[0]
            if section.left_margin:
                left_margin = section.left_margin.pt
            if section.right_margin:
                right_margin = section.right_margin.pt
            if section.top_margin:
                top_margin = section.top_margin.pt
            if section.bottom_margin:
                bottom_margin = section.bottom_margin.pt
    except Exception:
        pass
    
    xml_template = f'''<?xml version="1.0" encoding="UTF-8" ?> 

<template format_id="1.8" >
<content><![CDATA[{full_text}]]></content><properties><pageFormat mediaSizeName="1" leftMargin="{left_margin}" rightMargin="{right_margin}" topMargin="{top_margin}" bottomMargin="{bottom_margin}" paperOrientation="1" headerFOffset="20.0" footerFOffset="20.0" /></properties>
<elements resolver="hvl-default" >
{elements_xml}
</elements>
<styles><style name="default" description="Geçerli" family="Dialog" size="12" bold="false" italic="false" FONT_ATTRIBUTE_KEY="javax.swing.plaf.FontUIResource[family=Dialog,name=Dialog,style=plain,size=12]" foreground="-13421773" /><style name="hvl-default" family="Times New Roman" size="12" description="Gövde" /></styles>
</template>
'''
    return xml_template.encode('utf-8')

def estimate_alignment(bbox, page_width):
    x0, y0, x1, y1 = bbox
    mid_block = (x0 + x1) / 2
    mid_page = page_width / 2
    
    # Check if centered (midpoint within 25 pt)
    if abs(mid_block - mid_page) < 25 and (x1 - x0) < page_width * 0.75:
        return 1  # Center
    
    # Check if right aligned (right edge close to margin)
    if x0 > page_width * 0.4 and (page_width - x1) < 70:
        return 2  # Right
        
    return 0  # Left

def pdf_to_udf_xml(pdf_path):
    """Converts a PDF file to UDF content.xml raw bytes preserving formatting, alignments, and tables."""
    import fitz
    doc = fitz.open(pdf_path)
    
    content_list = []
    elements_list = []
    current_offset = 0
    
    for page in doc:
        page_width = page.rect.width
        
        # 1. Find tables on page
        tables = page.find_tables()
        table_list = list(tables)
            
        # 2. Extract text blocks using dictionary mode to retrieve spans with font details
        page_dict = page.get_text("dict")
        blocks = page_dict.get("blocks", [])
        
        # Filter out blocks that lie within detected tables
        text_blocks = []
        for b in blocks:
            if b.get("type") == 0:  # Text block
                bx0, by0, bx1, by1 = b["bbox"]
                in_table = False
                for t in table_list:
                    tx0, ty0, tx1, ty1 = t.bbox
                    # Overlap check using center point
                    bcx = (bx0 + bx1) / 2
                    bcy = (by0 + by1) / 2
                    if tx0 <= bcx <= tx1 and ty0 <= bcy <= ty1:
                        in_table = True
                        break
                if not in_table:
                    text_blocks.append(b)
                    
        # 3. Combine remaining text blocks and tables, sort by vertical position (y0)
        items_to_process = []
        for b in text_blocks:
            items_to_process.append(("text", b["bbox"][1], b))
        for t in table_list:
            items_to_process.append(("table", t.bbox[1], t))
            
        items_to_process.sort(key=lambda x: x[1])
        
        # 4. Process items in sorted order
        for item_type, _, item in items_to_process:
            if item_type == "text":
                block_lines = item.get("lines", [])
                align_val = estimate_alignment(item["bbox"], page_width)
                
                # Estimate line spacing dynamically from lines in PDF block
                line_spacing = 1.15
                if len(block_lines) > 1:
                    spacings = []
                    for idx_l in range(len(block_lines) - 1):
                        curr_l = block_lines[idx_l]
                        next_l = block_lines[idx_l+1]
                        curr_y = curr_l["bbox"][1]
                        next_y = next_l["bbox"][1]
                        curr_h = curr_l["bbox"][3] - curr_l["bbox"][1]
                        if curr_h > 0:
                            spacings.append((next_y - curr_y) / curr_h)
                    if spacings:
                        avg_spacing = sum(spacings) / len(spacings)
                        if 0.8 <= avg_spacing <= 2.5:
                            line_spacing = round(avg_spacing, 2)

                para_elements = []
                para_text = ""
                
                for line in block_lines:
                    for span in line.get("spans", []):
                        span_text = span["text"]
                        font_name = span["font"].lower()
                        size = round(span["size"])
                        
                        is_bold = "bold" in font_name or (span["flags"] & 4)
                        is_italic = "italic" in font_name or "oblique" in font_name or (span["flags"] & 2)
                        
                        bold_str = "true" if is_bold else "false"
                        italic_str = "true" if is_italic else "false"
                        
                        # Improved font family mapping
                        family = "Times New Roman"
                        if "arial" in font_name or "helvetica" in font_name:
                            family = "Arial"
                        elif "calibri" in font_name:
                            family = "Calibri"
                        elif "courier" in font_name:
                            family = "Courier New"
                        elif "tahoma" in font_name:
                            family = "Tahoma"
                        elif "verdana" in font_name:
                            family = "Verdana"
                        elif "georgia" in font_name:
                            family = "Georgia"
                            
                        # Extract color
                        pdf_color = span.get("color")
                        foreground_attr = ""
                        if pdf_color is not None:
                            r = (pdf_color >> 16) & 255
                            g = (pdf_color >> 8) & 255
                            b = pdf_color & 255
                            argb = (255 << 24) | (r << 16) | (g << 8) | b
                            signed_color = argb - 2**32 if argb >= 2**31 else argb
                            foreground_attr = f' foreground="{signed_color}"'
                        
                        para_elements.append(
                            f'<content startOffset="{current_offset}" length="{len(span_text)}" '
                            f'family="{family}" size="{size}" bold="{bold_str}" italic="{italic_str}"{foreground_attr} />'
                        )
                        para_text += span_text
                        current_offset += len(span_text)
                        
                    if block_lines.index(line) < len(block_lines) - 1:
                        if not para_text.endswith(" "):
                            para_elements.append(
                                f'<content startOffset="{current_offset}" length="1" family="Times New Roman" size="12" bold="false" italic="false" />'
                            )
                            para_text += " "
                            current_offset += 1
                            
                para_text += "\n"
                para_elements.append(
                    f'<content startOffset="{current_offset}" length="1" family="Times New Roman" size="12" bold="false" italic="false" />'
                )
                current_offset += 1
                
                p_xml = f'<paragraph Alignment="{align_val}" LineSpacing="{line_spacing}">{"".join(para_elements)}</paragraph>'
                content_list.append(para_text)
                elements_list.append(p_xml)
                
            elif item_type == "table":
                table_element_rows = []
                table_text = ""
                
                # Calculate column widths robustly from cell coordinate boundaries
                x_coords = set()
                for row in item.rows:
                    for cell_bbox in row.cells:
                        if cell_bbox:
                            x_coords.add(round(cell_bbox[0]))
                            x_coords.add(round(cell_bbox[2]))
                sorted_x = sorted(list(x_coords))
                col_widths = []
                for idx_c in range(len(sorted_x) - 1):
                    col_widths.append(sorted_x[idx_c+1] - sorted_x[idx_c])
                if not col_widths:
                    tbl_w = item.bbox[2] - item.bbox[0]
                    col_widths = [tbl_w]

                column_count = len(col_widths)
                total_width = sum(col_widths)
                if total_width == 0:
                    total_width = 1
                column_spans = ",".join([str(max(1, int((w / total_width) * 300))) for w in col_widths])
                
                table_data = item.extract()
                
                for row_idx, row in enumerate(item.rows):
                    cells = []
                    for col_idx, cell_bbox in enumerate(row.cells):
                        cell_text = ""
                        cell_elements = []
                        
                        cell_spans = []
                        if cell_bbox:
                            cx0, cy0, cx1, cy1 = cell_bbox
                            for block in blocks:
                                if block.get("type") == 0:
                                    for line in block.get("lines", []):
                                        for span in line.get("spans", []):
                                            sx0, sy0, sx1, sy1 = span["bbox"]
                                            scx = (sx0 + sx1) / 2
                                            scy = (sy0 + sy1) / 2
                                            if cx0 <= scx <= cx1 and cy0 <= scy <= cy1:
                                                cell_spans.append(span)
                                                
                        if cell_spans:
                            # Sort cell spans by y0 (top to bottom) then x0 (left to right)
                            cell_spans.sort(key=lambda s: (s["bbox"][1], s["bbox"][0]))
                            
                            # Group spans into lines inside the cell
                            lines_in_cell = []
                            current_line_spans = []
                            last_y0 = None
                            for span in cell_spans:
                                y0 = span["bbox"][1]
                                if last_y0 is None:
                                    current_line_spans.append(span)
                                    last_y0 = y0
                                else:
                                    span_h = span["bbox"][3] - span["bbox"][1]
                                    if abs(y0 - last_y0) > max(3.0, span_h * 0.5):
                                        lines_in_cell.append(current_line_spans)
                                        current_line_spans = [span]
                                        last_y0 = y0
                                    else:
                                        current_line_spans.append(span)
                            if current_line_spans:
                                lines_in_cell.append(current_line_spans)

                            # Parse each line in the cell
                            for line_spans in lines_in_cell:
                                para_elems = []
                                para_txt = ""
                                for span in line_spans:
                                    span_text = span["text"]
                                    font_name = span["font"].lower()
                                    size = round(span["size"])
                                    
                                    is_bold = "bold" in font_name or (span["flags"] & 4)
                                    is_italic = "italic" in font_name or (span["flags"] & 2)
                                    
                                    bold_str = "true" if is_bold else "false"
                                    italic_str = "true" if is_italic else "false"
                                    
                                    family = "Times New Roman"
                                    font_name_lower = font_name.lower()
                                    if "arial" in font_name_lower or "helvetica" in font_name_lower:
                                        family = "Arial"
                                    elif "calibri" in font_name_lower:
                                        family = "Calibri"
                                    elif "courier" in font_name_lower:
                                        family = "Courier New"
                                    elif "tahoma" in font_name_lower:
                                        family = "Tahoma"
                                    elif "verdana" in font_name_lower:
                                        family = "Verdana"
                                        
                                    pdf_color = span.get("color")
                                    foreground_attr = ""
                                    if pdf_color is not None:
                                        r = (pdf_color >> 16) & 255
                                        g = (pdf_color >> 8) & 255
                                        b = pdf_color & 255
                                        argb = (255 << 24) | (r << 16) | (g << 8) | b
                                        signed_color = argb - 2**32 if argb >= 2**31 else argb
                                        foreground_attr = f' foreground="{signed_color}"'
                                        
                                    para_elems.append(
                                        f'<content startOffset="{current_offset}" length="{len(span_text)}" '
                                        f'family="{family}" size="{size}" bold="{bold_str}" italic="{italic_str}"{foreground_attr} />'
                                    )
                                    para_txt += span_text
                                    current_offset += len(span_text)
                                    
                                para_txt += "\n"
                                para_elems.append(
                                    f'<content startOffset="{current_offset}" length="1" family="Times New Roman" size="10" bold="false" italic="false" />'
                                )
                                current_offset += 1
                                
                                cell_text += para_txt
                                cell_elements.append(f'<paragraph Alignment="0">{"".join(para_elems)}</paragraph>')
                        else:
                            text_val = ""
                            if row_idx < len(table_data) and col_idx < len(table_data[row_idx]):
                                text_val = table_data[row_idx][col_idx] or ""
                            
                            text_val = text_val.strip()
                            if text_val:
                                para_elems = [
                                    f'<content startOffset="{current_offset}" length="{len(text_val)}" '
                                    f'family="Times New Roman" size="10" bold="false" italic="false" />'
                                ]
                                current_offset += len(text_val)
                                
                                text_val += "\n"
                                para_elems.append(
                                    f'<content startOffset="{current_offset}" length="1" family="Times New Roman" size="10" bold="false" italic="false" />'
                                )
                                current_offset += 1
                                
                                cell_text += text_val
                                cell_elements.append(f'<paragraph Alignment="0">{"".join(para_elems)}</paragraph>')
                            else:
                                cell_text += "\n"
                                cell_elements.append(
                                    f'<paragraph Alignment="0"><content startOffset="{current_offset}" length="1" family="Times New Roman" size="10" bold="false" italic="false" /></paragraph>'
                                )
                                current_offset += 1
                                
                        cells.append(f'<cell>{"".join(cell_elements)}</cell>')
                        table_text += cell_text
                        
                    table_element_rows.append(f'<row rowName="row{row_idx + 1}" rowType="dataRow">{"".join(cells)}</row>')
                    
                table_xml = f'<table tableName="Sabit" columnCount="{column_count}" columnSpans="{column_spans}" border="borderCell">{"".join(table_element_rows)}</table>'
                
                content_list.append(table_text)
                elements_list.append(table_xml)

    if not content_list:
        content_list.append("\n")
        elements_list.append(f'<paragraph Alignment="0"><content startOffset="0" length="1" family="Times New Roman" size="12" bold="false" italic="false" /></paragraph>')

    full_text = "".join(content_list)
    elements_xml = "\n".join(elements_list)
    
    xml_template = f'''<?xml version="1.0" encoding="UTF-8" ?> 

<template format_id="1.8" >
<content><![CDATA[{full_text}]]></content><properties><pageFormat mediaSizeName="1" leftMargin="42.525000000000006" rightMargin="42.525000000000006" topMargin="42.525000000000006" bottomMargin="42.52500000000006" paperOrientation="1" headerFOffset="20.0" footerFOffset="20.0" /></properties>
<elements resolver="hvl-default" >
{elements_xml}
</elements>
<styles><style name="default" description="Geçerli" family="Dialog" size="12" bold="false" italic="false" FONT_ATTRIBUTE_KEY="javax.swing.plaf.FontUIResource[family=Dialog,name=Dialog,style=plain,size=12]" foreground="-13421773" /><style name="hvl-default" family="Times New Roman" size="12" description="Gövde" /></styles>
</template>
'''
    return xml_template.encode('utf-8')

def convert_to_udf_xml(input_path):
    """Wrapper function to convert docx, doc, or pdf to content.xml raw bytes."""
    _, ext = os.path.splitext(input_path.lower())
    if ext == '.docx':
        return docx_to_udf_xml(input_path)
    elif ext == '.doc':
        docx_temp = doc_to_docx(input_path)
        try:
            return docx_to_udf_xml(docx_temp)
        finally:
            try:
                os.remove(docx_temp)
            except Exception:
                pass
    elif ext == '.pdf':
        return pdf_to_udf_xml(input_path)
    else:
        raise ValueError("Desteklenmeyen dosya uzantısı. Yalnızca .docx, .doc ve .pdf desteklenir.")
