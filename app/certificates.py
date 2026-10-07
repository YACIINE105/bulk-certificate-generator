from html import escape
from pathlib import Path


def generate_certificate(job: dict, recipient: dict, certificate_id: str, output: Path):
    """One printable HTML template; escape all user-provided content."""
    title, course, issuer, issued_on, name, email = (
        escape(str(value), quote=True)
        for value in (
            job["title"],
            job["course"],
            job["issuer"],
            job["issued_on"],
            recipient["name"],
            recipient["email"],
        )
    )
    document = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{title}</title>
<style>
body {{ margin: 0; background: #f3f4f6; color: #172554; font-family: Georgia, serif; }}
main {{ box-sizing: border-box; max-width: 1000px; margin: 40px auto; padding: 60px;
        border: 8px double #172554; background: white; text-align: center; }}
h1 {{ font-size: 38px; overflow-wrap: anywhere; }}
h2 {{ font-size: 34px; overflow-wrap: anywhere; }}
p {{ font-size: 20px; overflow-wrap: anywhere; }}
small {{ display: block; margin-top: 32px; overflow-wrap: anywhere; }}
@page {{ size: A4 landscape; margin: 12mm; }}
@media print {{ body {{ background: white; }} main {{ margin: 0; max-width: none; }} }}
</style></head>
<body><main>
<h1>{title}</h1><p>This certificate is presented to</p>
<h2 dir="auto">{name}</h2><p>{email}</p>
<p>For completing</p><h2 dir="auto">{course}</h2>
<p>Issued by {issuer}</p><p>Date: {issued_on}</p>
<small>Certificate ID: {certificate_id}</small>
</main></body></html>"""
    temporary = output.with_suffix(".tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(output)
