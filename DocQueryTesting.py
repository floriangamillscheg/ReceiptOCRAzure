from docquery import document, pipeline




p = pipeline('document-question-answering')
doc = document.load_document("05910591.jpg")
for q in ["What is the Total?", "What is the UID number?"]:
    print(q, p(question=q, **doc.context))