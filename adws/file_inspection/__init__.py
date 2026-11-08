"""file_inspection: turn a file that landed on EG's machine into something a remote agent can look at.

A downloaded .tif is useless to ChatGPT while it sits on EG's disk: ChatGPT cannot read a TIFF and
cannot reach the disk. This package makes, beside the untouched original, page images (JPEG, or PNG
for black-and-white line art) and extracted text, which the Bridge worker uploads and the gateway's
`browser_view_file` hands back as MCP image and text content. Read DESIGN.md first.
"""
