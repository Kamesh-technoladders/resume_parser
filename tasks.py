import os
from pdf2image import convert_from_path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from tenacity import retry, stop_after_attempt, wait_exponential
import logging
import json
import pytesseract
from pdf2image.exceptions import PDFPageCountError
from reportlab.lib import colors

# Import shared objects from config.py
from config import gemini_model, supabase, SUPABASE_STORAGE_BUCKET, SUPABASE_REPORT_PATH_PREFIX, redis_conn

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Function to log progress and store in Redis
def log_progress(job_id: str, step: str, message: str, data: dict = None):
    """Log a progress step and store it in Redis."""
    log_entry = {
        "step": step,
        "message": message,
        "data": data or {},
        "timestamp": str(os.times()[4])  # System time
    }
    logger.info(f"Job {job_id} - {step}: {message}")
    redis_conn.rpush(f"job_logs:{job_id}", json.dumps(log_entry))

# Helper function to download resume from Supabase Storage
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def download_resume(resume_path: str) -> str:
    try:
        response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).download(resume_path)
        local_path = f"/tmp/{os.path.basename(resume_path)}"
        with open(local_path, "wb") as f:
            f.write(response)
        return local_path
    except Exception as e:
        raise Exception(f"Failed to download resume from Supabase: {str(e)}")

# Helper function to get the total number of pages in the PDF
def get_pdf_page_count(pdf_path: str) -> int:
    try:
        from pdf2image import pdfinfo_from_path
        pdf_info = pdfinfo_from_path(pdf_path)
        return int(pdf_info["Pages"])
    except PDFPageCountError as e:
        raise Exception(f"Failed to determine PDF page count: {str(e)}")

# Helper function to extract text from PDF using OCR, processing one page at a time
def extract_text_from_pdf(pdf_path: str, job_id: str) -> str:
    try:
        total_pages = get_pdf_page_count(pdf_path)
        log_progress(job_id, "extract_text", f"PDF has {total_pages} pages")

        text = ""
        custom_config = r'--oem 3 --psm 6'  # OEM 3 (default), PSM 6 (assume a single uniform block of text)

        for page_num in range(1, total_pages + 1):
            log_progress(job_id, "extract_text", f"Converting page {page_num}/{total_pages} to image at 200 DPI")
            images = convert_from_path(pdf_path, dpi=200, first_page=page_num, last_page=page_num)
            if not images:
                log_progress(job_id, "extract_text", f"No image generated for page {page_num}")
                continue

            page_text = pytesseract.image_to_string(images[0], config=custom_config)
            text += f"\n--- Page {page_num} ---\n{page_text}"
            log_progress(job_id, "extract_text", f"Extracted text from page {page_num}/{total_pages}", {
                "page_text_length": len(page_text),
                "page_text": page_text[:500]  # Log first 500 chars for debugging (avoid Redis size limits)
            })

            del images

        log_progress(job_id, "extract_text", "Text extracted successfully", {
            "text_length": len(text),
            "full_text": text[:1000]  # Log first 1000 chars of full text for debugging
        })
        return text.strip() or "No text extracted"
    except Exception as e:
        raise Exception(f"Failed to extract text from PDF: {str(e)}")

# Helper function to clean Gemini AI output by removing Markdown headings
def clean_gemini_output(text: str) -> str:
    lines = text.split("\n")
    cleaned_lines = [line for line in lines if not line.strip().startswith("##")]
    return "\n".join(cleaned_lines).strip()

# ... [previous imports and unchanged functions remain the same] ...

# Helper function to generate the overall report using a single Gemini API call
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def generate_report(resume_text: str, job_description: str) -> dict:
    try:
        # Log the input to Gemini for debugging
        log_progress("debug", "generate_report_input", "Sending to Gemini", {
            "resume_text": resume_text[:1000],
            "job_description": job_description[:1000]
        })

        prompt = f"""
        Analyze the following resume against the job description and provide a detailed suitability report for HR staffing purposes. The report should be structured, clear, and concise, with no asterisks (**) around headings or content. Focus on validating the candidate’s skills, experience, and education with high specificity—e.g., identify exact skills used in named projects (like 'Robotic Mechanic') and contributions (like 'Implemented servo control with Python'). Explain the scoring rationale for each section in detail, tying it to specific evidence from the resume and job description. Ensure the output fits within an A4 page context and suggest precise, actionable ways to enhance the candidate’s score. Follow these instructions precisely:

        1. **Candidate Details**:
           - Extract: Name, Phone Number, Email, LinkedIn URL, GitHub URL from the resume. If not found, write "Not provided".

        2. **Sectional Analysis (Skills, Work Experience, Relevant Projects, Education)**:
           - Matches: List specific items from the job description that match the resume, with detailed validation (e.g., "Java - Listed under skills and used in 'Robotic Mechanic' project for backend logic"). Include examples where possible.
           - Non-Matches: List key items from the job description missing in the resume, with specific explanation (e.g., "AWS - Required for cloud deployment, but no mention of cloud experience in resume or projects").
           - Summary: Provide a detailed, specific summary of alignment, including strengths (e.g., "Vinay’s Java expertise shines in Robotic Mechanic") and gaps (e.g., "Lacks cloud skills critical for this role"). Avoid vague phrases like "good fit" or "some experience"—use precise evidence.
           - Score: Assign a score out of 100 based on relevance and completeness:
             - Skills: 100 if all key skills match with evidence of use (e.g., in projects), 0 if none match, proportional otherwise (e.g., 75 if 3/4 skills match with examples).
             - Work Experience: 100 if experience fully aligns in duration and tech (e.g., "6 years in Java aligns with 6-9 years required"), 0 if irrelevant (e.g., "Only sales experience").
             - Relevant Projects: 100 if projects directly match job needs with named examples (e.g., "Robotic Mechanic used Java and SQL"), 0 if none are relevant.
             - Education: 100 if fully meets requirements (e.g., "B.Tech in Mechanical Engineering matches robotics focus"), 0 if irrelevant.
           - Enhancement Tips: Suggest specific, actionable improvements (e.g., "Gain AWS certification and add a cloud-based project to GitHub").
           - Example: For a Java Developer JD requiring Java, Spring, and 3+ years:
             - Matches: "Java - Used in 'Robotic Mechanic' for control logic; Spring - Implemented in 'E-commerce App' backend."
             - Non-Matches: "AWS - No cloud experience mentioned."
             - Summary: "Vinay’s 4 years of Java and Spring experience in named projects like Robotic Mechanic align well, but AWS is a gap."
             - Score: "75/100 - Strong Java and Spring, missing AWS."
             - Tips: "Complete an AWS project and list it."

        3. **Overall Summary and Score**:
           - Summary: Summarize the candidate’s suitability with specific highlights (e.g., "Vinay’s 4-year Java experience in Robotic Mechanic is a strength, but missing AWS limits cloud suitability") and gaps from an HR perspective. Keep it 2-3 sentences, detailed, and evidence-based.
           - Overall Score: Calculate as a weighted average:
             - Skills (30%) + Work Experience (20%) + Relevant Projects (30%) + Education (20%).
             - Formula: (Skills_Score * 0.3) + (Work_Exp_Score * 0.2) + (Projects_Score * 0.3) + (Education_Score * 0.2).
             - Return the score as a float with one decimal place (e.g., 63.5).
           - Example: "Vinay’s Java and Spring skills in Robotic Mechanic are strong, but lack of AWS and limited project diversity lower his fit. Overall Score: 72.5/100."

        Resume: {resume_text}
        Job Description: {job_description}

        Return the report in this exact format:
        Candidate Details:
        - Name: [Extracted Name]
        - Phone Number: [Extracted Phone]
        - Email: [Extracted Email]
        - LinkedIn: [Extracted LinkedIn or Not provided]
        - GitHub: [Extracted GitHub or Not provided]

        Skill Match:
        - Matches: [List with detailed validation, e.g., "Python - Used in 'Robotic Mechanic' for servo control"]
        - Non-Matches: [List with specific explanation, e.g., "Kubernetes - No containerization experience noted"]
        - Summary: [Detailed, evidence-based summary, e.g., "Vinay’s Python and Java skills from Robotic Mechanic align with automation needs, but Kubernetes is missing"]
        - Score: [Score]/100 - [Detailed rationale, e.g., "80/100 - Matches 4/5 skills with project evidence, missing Kubernetes"]
        - Enhancement Tips: [Specific suggestions, e.g., "Learn Kubernetes via a Udemy course and deploy a sample project"]

        Work Experience:
        - Matches: [List with details, e.g., "4 years at XYZ Corp - Worked on 'Robotic Mechanic' using Java, matching 3+ year requirement"]
        - Non-Matches: [List with explanation, e.g., "No team lead experience - JD requires leadership, not mentioned"]
        - Summary: [Detailed summary, e.g., "Vinay’s 4 years at XYZ Corp on Robotic Mechanic shows strong technical fit, but lacks leadership experience"]
        - Score: [Score]/100 - [Rationale, e.g., "85/100 - Exceeds duration, missing leadership"]
        - Enhancement Tips: [Suggestions, e.g., "Take on a team lead role in next project"]

        Relevant Projects:
        - Matches: [List with details, e.g., "Robotic Mechanic - Implemented Java and SQL for control systems, matches JD’s automation focus"]
        - Non-Matches: [List with explanation, e.g., "No cloud projects - JD requires AWS experience"]
        - Summary: [Detailed summary, e.g., "Robotic Mechanic demonstrates relevant Java and SQL skills, but no cloud projects limit broader fit"]
        - Score: [Score]/100 - [Rationale, e.g., "70/100 - One strong match, missing cloud scope"]
        - Enhancement Tips: [Suggestions, e.g., "Build an AWS-hosted automation project"]

        Education:
        - Matches: [List with details, e.g., "B.Tech in Mechanical Engineering - Matches robotics JD requirement"]
        - Non-Matches: [List with explanation, e.g., "No advanced degree - JD prefers M.Tech, not required"]
        - Summary: [Detailed summary, e.g., "Vinay’s B.Tech aligns with robotics focus, though an M.Tech could enhance credentials"]
        - Score: [Score]/100 - [Rationale, e.g., "90/100 - Meets core requirement, lacks preferred advanced degree"]
        - Enhancement Tips: [Suggestions, e.g., "Pursue an M.Tech or relevant certification"]

        Overall Summary:
        - [Concise, specific summary, e.g., "Vinay’s Java and SQL expertise in Robotic Mechanic suits the role, but gaps in AWS and leadership reduce fit"]
        - Overall Score: [Weighted Score]/100
        """

        response = gemini_model.generate_content(prompt)
        gemini_output = clean_gemini_output(response.text)

        # Log Gemini's raw output for debugging
        log_progress("debug", "generate_report_output", "Gemini response", {
            "raw_output": gemini_output[:1000]
        })

        # Initialize the report with default values
        report = {
            "Candidate Details": {
                "Name": "Not provided",
                "Phone Number": "Not provided",
                "Email": "Not provided",
                "LinkedIn": "Not provided",
                "GitHub": "Not provided"
            },
            "Skill Match": {
                "Matches": "Not found",
                "Non-Matches": "Not found",
                "Summary": "Not found",
                "Score": 0,
                "Enhancement Tips": "Not found"
            },
            "Work Experience": {
                "Matches": "Not found",
                "Non-Matches": "Not found",
                "Summary": "Not found",
                "Score": 0,
                "Enhancement Tips": "Not found"
            },
            "Relevant Projects": {
                "Matches": "Not found",
                "Non-Matches": "Not found",
                "Summary": "Not found",
                "Score": 0,
                "Enhancement Tips": "Not found"
            },
            "Education": {
                "Matches": "Not found",
                "Non-Matches": "Not found",
                "Summary": "Not found",
                "Score": 0,
                "Enhancement Tips": "Not found"
            },
            "Overall Summary": "Not found",
            "Overall Score": 0.0
        }

        # Parse the Gemini response
        current_section = None
        matches_lines = []
        non_matches_lines = []
        summary_lines = []
        enhancement_tips_lines = []

        for line in gemini_output.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Section headers
            if "Candidate Details:" in line:
                current_section = "Candidate Details"
            elif "Skill Match:" in line:
                current_section = "Skill Match"
                matches_lines, non_matches_lines, summary_lines, enhancement_tips_lines = [], [], [], []
            elif "Work Experience:" in line:
                current_section = "Work Experience"
                matches_lines, non_matches_lines, summary_lines, enhancement_tips_lines = [], [], [], []
            elif "Relevant Projects:" in line:
                current_section = "Relevant Projects"
                matches_lines, non_matches_lines, summary_lines, enhancement_tips_lines = [], [], [], []
            elif "Education:" in line:
                current_section = "Education"
                matches_lines, non_matches_lines, summary_lines, enhancement_tips_lines = [], [], [], []
            elif "Overall Summary:" in line:
                current_section = "Overall Summary"
                summary_lines = []
            elif "Overall Score:" in line:
                score_str = line.split("Overall Score:", 1)[1].strip().split("/")[0].strip()
                report["Overall Score"] = float(score_str) if score_str.replace(".", "").isdigit() else 0.0

            # Parse section content
            elif current_section == "Candidate Details":
                for key in report["Candidate Details"].keys():
                    field = f"- {key}:"
                    if field in line:
                        report["Candidate Details"][key] = line.split(field, 1)[1].strip()
                        break
            elif current_section in ["Skill Match", "Work Experience", "Relevant Projects", "Education"]:
                if "Matches:" in line:
                    matches_lines.append(line.split("Matches:", 1)[1].strip())
                elif "Non-Matches:" in line:
                    non_matches_lines.append(line.split("Non-Matches:", 1)[1].strip())
                elif "Summary:" in line:
                    summary_lines.append(line.split("Summary:", 1)[1].strip())
                elif "Score:" in line:
                    score_str = line.split("Score:", 1)[1].strip().split("/")[0].strip()
                    report[current_section]["Score"] = int(score_str) if score_str.isdigit() else 0
                elif "Enhancement Tips:" in line:
                    enhancement_tips_lines.append(line.split("Enhancement Tips:", 1)[1].strip())
                elif matches_lines and "Non-Matches:" not in line and "Summary:" not in line:
                    matches_lines.append(line.strip())
                elif non_matches_lines and "Summary:" not in line and "Score:" not in line:
                    non_matches_lines.append(line.strip())
                elif summary_lines and "Score:" not in line and "Enhancement Tips:" not in line:
                    summary_lines.append(line.strip())
                elif enhancement_tips_lines and "Score:" not in line:
                    enhancement_tips_lines.append(line.strip())

                report[current_section]["Matches"] = " ".join(matches_lines).strip()
                report[current_section]["Non-Matches"] = " ".join(non_matches_lines).strip()
                report[current_section]["Summary"] = " ".join(summary_lines).strip()
                report[current_section]["Enhancement Tips"] = " ".join(enhancement_tips_lines).strip()
            elif current_section == "Overall Summary" and "Overall Score:" not in line:
                summary_lines.append(line.replace("Overall Summary:", "").strip())
                report["Overall Summary"] = " ".join(summary_lines).strip()

        # Log the parsed report for debugging
        log_progress("debug", "generate_report_parsed", "Parsed report", {
            "report": report
        })

        return report

    except Exception as e:
        log_progress("debug", "generate_report_error", f"Failed to generate report: {str(e)}")
        raise Exception(f"Failed to generate report with Gemini: {str(e)}")
# Helper function to save report as PDF with margins and line wrapping
def save_report_as_pdf(report: dict, output_path: str):
    try:
        c = canvas.Canvas(output_path, pagesize=letter)
        width, height = letter  # A4 size: 612 x 792 points
        left_margin, right_margin, top_margin, bottom_margin = 72, 72, 72, 72  # 1 inch margins (72 points)
        text_width = width - left_margin - right_margin  # Usable width for text
        y_position = height - top_margin  # Start below top margin

        # Helper function to add text with wrapping
        def add_text(text, y, bold=False, indent=0):
            nonlocal y_position
            if y_position < bottom_margin:  # New page if below bottom margin
                c.showPage()
                y_position = height - top_margin

            text_object = c.beginText(left_margin + indent, y_position)
            if bold:
                c.setFont("Helvetica-Bold", 12)
            else:
                c.setFont("Helvetica", 12)

            # Split text into lines that fit within text_width
            words = text.split()
            current_line = []
            for word in words:
                test_line = " ".join(current_line + [word])
                if c.stringWidth(test_line, "Helvetica" if not bold else "Helvetica-Bold", 12) <= text_width - indent:
                    current_line.append(word)
                else:
                    text_object.textLine(" ".join(current_line))
                    y_position -= 15
                    if y_position < bottom_margin:
                        c.drawText(text_object)
                        c.showPage()
                        y_position = height - top_margin
                        text_object = c.beginText(left_margin + indent, y_position)
                        c.setFont("Helvetica" if not bold else "Helvetica-Bold", 12)
                    current_line = [word]
            if current_line:
                text_object.textLine(" ".join(current_line))
                y_position -= 15

            c.drawText(text_object)

        # Title
        add_text("Resume Analysis Report", y_position, bold=True)
        y_position -= 20

        # Candidate Details
        add_text("Candidate Details", y_position, bold=True)
        for key, value in report["Candidate Details"].items():
            add_text(f"- {key}: {value}", y_position, indent=10)
        y_position -= 20

        # Skill Match
        add_text("Skill Match", y_position, bold=True)
        add_text(f"Score: {report['Skill Match']['Score']}/100", y_position, indent=10)
        add_text(f"Matches: {report['Skill Match']['Matches']}", y_position, indent=10)
        add_text(f"Non-Matches: {report['Skill Match']['Non-Matches']}", y_position, indent=10)
        add_text(f"Summary: {report['Skill Match']['Summary']}", y_position, indent=10)
        
        # add_text(f"Enhancement Tips: {report['Skill Match']['Enhancement Tips']}", y_position, indent=10)
        y_position -= 20

        # Work Experience
        add_text("Work Experience", y_position, bold=True)
        add_text(f"Score: {report['Work Experience']['Score']}/100", y_position, indent=10)
        add_text(f"Matches: {report['Work Experience']['Matches']}", y_position, indent=10)
        add_text(f"Non-Matches: {report['Work Experience']['Non-Matches']}", y_position, indent=10)
        add_text(f"Summary: {report['Work Experience']['Summary']}", y_position, indent=10)
        # add_text(f"Enhancement Tips: {report['Work Experience']['Enhancement Tips']}", y_position, indent=10)
        y_position -= 20

        # Relevant Projects (mapped to Relevant Work Experience)
        add_text("Relevant Projects", y_position, bold=True)
        add_text(f"Score: {report['Relevant Projects']['Score']}/100", y_position, indent=10)
        add_text(f"Matches: {report['Relevant Projects']['Matches']}", y_position, indent=10)
        add_text(f"Non-Matches: {report['Relevant Projects']['Non-Matches']}", y_position, indent=10)
        add_text(f"Summary: {report['Relevant Projects']['Summary']}", y_position, indent=10)
        # add_text(f"Enhancement Tips: {report['Relevant Projects']['Enhancement Tips']}", y_position, indent=10)
        y_position -= 20

        # Education
        add_text("Education", y_position, bold=True)
        add_text(f"Score: {report['Education']['Score']}/100", y_position, indent=10)
        add_text(f"Matches: {report['Education']['Matches']}", y_position, indent=10)
        add_text(f"Non-Matches: {report['Education']['Non-Matches']}", y_position, indent=10)
        add_text(f"Summary: {report['Education']['Summary']}", y_position, indent=10)
        
        # add_text(f"Enhancement Tips: {report['Education']['Enhancement Tips']}", y_position, indent=10)
        y_position -= 20

        # Overall Summary
        add_text("Overall Summary", y_position, bold=True)
        add_text(report["Overall Summary"], y_position, indent=10)
        add_text(f"Overall Score: {report['Overall Score']}/100", y_position, indent=10)

        c.save()
    except Exception as e:
        raise Exception(f"Failed to save report as PDF: {str(e)}")

# ... [rest of the unchanged functions] ...# Helper function to upload report to Supabase Storage
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def upload_report(report_path: str, destination_path: str):
    try:
        with open(report_path, "rb") as f:
            response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).upload(destination_path, f)
            # Check response for errors
            if hasattr(response, 'status_code') and response.status_code != 200:
                raise Exception(f"Upload failed with status {response.status_code}: {response.json()}")
        return response
    except Exception as e:
        log_progress("upload_error", f"Failed to upload report to Supabase: {str(e)}")
        raise Exception(f"Failed to upload report to Supabase: {str(e)}")

# Background task to process the analysis
def process_analysis(job_id: str, candidate_id: str, resume_path: str, job_description: str):
    local_resume_path = None
    local_report_path = None
    try:
        # Log task initiation
        log_progress(job_id, "init", "Task started", {
            "candidate_id": candidate_id,
            "resume_path": resume_path,
            "job_description": job_description
        })

        # Validate job_id exists in hr_jobs
        job_response = supabase.table("hr_jobs").select("description").eq("id", job_id).execute()
        if not job_response.data:
            raise Exception(f"Job {job_id} not found in hr_jobs")
        job_description = job_response.data[0]["description"]  # Use DB description if provided

        # Download resume
        log_progress(job_id, "download_resume", "Downloading resume from Supabase Storage")
        local_resume_path = download_resume(resume_path)
        log_progress(job_id, "download_resume", "Resume downloaded successfully", {
            "local_path": local_resume_path,
            "resume_size": os.path.getsize(local_resume_path)
        })

        # Extract text from resume
        log_progress(job_id, "extract_text", "Extracting text from resume using OCR")
        resume_text = extract_text_from_pdf(local_resume_path, job_id)
        log_progress(job_id, "extract_text", "Text extracted successfully", {
            "text_length": len(resume_text)
        })

        # Generate report
        log_progress(job_id, "generate_report", "Generating report with Gemini")
        report = generate_report(resume_text, job_description)
        log_progress(job_id, "generate_report", "Report generated successfully", {
            "overall_score": report["Overall Score"],
            "candidate_details": report["Candidate Details"]
        })

        # Ensure candidate exists in hr_candidates
        candidate_response = supabase.table("hr_candidates").select("*").eq("id", candidate_id).execute()
        if not candidate_response.data:
            # Safely handle missing Candidate Details
            candidate_details = report.get("Candidate Details", {})
            supabase.table("hr_candidates").insert({
                "id": candidate_id,
                "name": candidate_details.get("Name", "Unknown"),
                "email": candidate_details.get("Email", f"unknown_{candidate_id}@example.com"),
                "phone_number": candidate_details.get("Phone Number", None),
                "linkedin_url": candidate_details.get("LinkedIn", None),
                "github_url": candidate_details.get("GitHub", None)
            }).execute()
            log_progress(job_id, "create_candidate", f"Created new candidate record for {candidate_id}")

        # Save report as PDF
        log_progress(job_id, "save_report", "Saving report as PDF")
        report_filename = f"report_{job_id}_{candidate_id}.pdf"
        local_report_path = f"/tmp/{report_filename}"
        save_report_as_pdf(report, local_report_path)
        log_progress(job_id, "save_report", "Report saved successfully", {
            "local_report_path": local_report_path
        })

        # Upload report to Supabase Storage
        log_progress(job_id, "upload_report", "Uploading report to Supabase Storage")
        report_destination_path = f"{SUPABASE_REPORT_PATH_PREFIX}/{job_id}/{report_filename}"
        upload_report(local_report_path, report_destination_path)
        log_progress(job_id, "upload_report", "Report uploaded successfully", {
            "report_destination_path": report_destination_path
        })

        # Check if hr_job_candidates record exists, insert if not, update if it does
        # Update hr_job_candidates for existing record
        log_progress(job_id, "update_candidate", f"Updating hr_job_candidates for id: {candidate_id}")
        existing_record = supabase.table("hr_job_candidates").select("id").eq("id", candidate_id).execute()
        
        if not existing_record.data:
            raise Exception(f"No hr_job_candidates record found for id: {candidate_id}")

        # Get candidate name from report or hr_candidates
        candidate_name = report.get("Candidate Details", {}).get("Name", None)
        if not candidate_name:
            # Fallback: Query hr_candidates (assuming name might still come from there)
            candidate_response = supabase.table("hr_candidates").select("name").eq("id", candidate_id).execute()
            candidate_name = candidate_response.data[0]["name"] if candidate_response.data else "Unknown"
        
        candidate_data = {
            "job_id": job_id,
            "candidate_id": candidate_id,
            # "resume_url": resume_path,
            # "report_url": report_destination_path,
            "status": "finished",
            # "name": candidate_name,
            "overall_score": report.get("Overall Score", 0.0),
            "skills_score": report.get("Skill Match", {}).get("Score", 0),
            "skills_summary": report.get("Skill Match", {}).get("Summary", ""),
            "skills_enhancement_tips": report.get("Skill Match", {}).get("Enhancement Tips", ""),
            "work_experience_score": report.get("Work Experience", {}).get("Score", 0),
            "work_experience_summary": report.get("Work Experience", {}).get("Summary", ""),
            "work_experience_enhancement_tips": report.get("Work Experience", {}).get("Enhancement Tips", ""),
            "projects_score": report.get("Relevant Projects", {}).get("Score", 0),
            "projects_summary": report.get("Relevant Projects", {}).get("Summary", ""),
            "projects_enhancement_tips": report.get("Relevant Projects", {}).get("Enhancement Tips", ""),
            "education_score": report.get("Education", {}).get("Score", 0),
            "education_summary": report.get("Education", {}).get("Summary", ""),
            "education_enhancement_tips": report.get("Education", {}).get("Enhancement Tips", ""),
            "overall_summary": report.get("Overall Summary", "")
        }

       # Update existing record where id matches candidate_id
        supabase.table("hr_job_candidates").update(candidate_data).eq("id", candidate_id).execute()
        log_progress(job_id, "update_candidate", f"Updated hr_job_candidates record for id: {candidate_id}")

        return {"status": "finished", "candidate_id": candidate_id}

    except Exception as e:
        log_progress(job_id, "error", f"Task failed: {str(e)}")
        # Update status to failed if record exists
        existing_record = supabase.table("hr_job_candidates").select("id").eq("job_id", job_id).eq("candidate_id", candidate_id).execute()
        if existing_record.data:
            supabase.table("hr_job_candidates").update({"status": "failed"}).eq("job_id", job_id).eq("candidate_id", candidate_id).execute()
        return {"status": "failed", "candidate_id": candidate_id, "error": str(e)}

    finally:
        try:
            log_progress(job_id, "cleanup", "Cleaning up temporary files")
            if local_resume_path and os.path.exists(local_resume_path):
                os.remove(local_resume_path)
            if local_report_path and os.path.exists(local_report_path):
                os.remove(local_report_path)
            log_progress(job_id, "cleanup", "Temporary files removed successfully")
        except Exception as e:
            log_progress(job_id, "cleanup_error", f"Failed to clean up temporary files: {str(e)}")