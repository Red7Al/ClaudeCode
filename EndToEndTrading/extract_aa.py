import zipfile, re, sys
sys.stdout.reconfigure(encoding='utf-8')

with zipfile.ZipFile(r'C:\Users\eahin\OneDrive\TradingWithAdam\A&A Trading Summary-v1.06.docx') as z:
    xml = z.read('word/document.xml').decode('utf-8')
    rels = z.read('word/_rels/document.xml.rels').decode('utf-8')
    media = [n for n in z.namelist() if n.startswith('word/media/')]

clean = re.sub(r'<[^>]+>', ' ', xml)
clean = re.sub(r'\s+', ' ', clean)

print('=== SECTION 26000-38000 ===')
print(clean[26000:38000])
print()
print('=== MEDIA FILES ===')
for m in media:
    print(m)
