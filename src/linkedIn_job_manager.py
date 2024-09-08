import json
import os
import random
import time
from itertools import product
from pathlib import Path

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

import src.utils as utils
from src.job import Job
from src.linkedIn_easy_applier import LinkedInEasyApplier
from src.utils import logger


class EnvironmentKeys:
    def __init__(self):
        logger.debug("Initializing EnvironmentKeys")
        self.skip_apply = self._read_env_key_bool("SKIP_APPLY")
        self.disable_description_filter = self._read_env_key_bool("DISABLE_DESCRIPTION_FILTER")
        logger.debug("EnvironmentKeys initialized: skip_apply=%s, disable_description_filter=%s",
                     self.skip_apply, self.disable_description_filter)

    @staticmethod
    def _read_env_key(key: str) -> str:
        value = os.getenv(key, "")
        logger.debug("Read environment key %s: %s", key, value)
        return value

    @staticmethod
    def _read_env_key_bool(key: str) -> bool:
        value = os.getenv(key) == "True"
        logger.debug("Read environment key %s as bool: %s", key, value)
        return value


class LinkedInJobManager:
    def __init__(self, driver):
        logger.debug("Initializing LinkedInJobManager")
        self.driver = driver
        self.set_old_answers = set()
        self.easy_applier_component = None
        logger.debug("LinkedInJobManager initialized successfully")

    def set_parameters(self, parameters):
        logger.debug("Setting parameters for LinkedInJobManager")
        self.company_blacklist = parameters.get('companyBlacklist', []) or []
        self.title_blacklist = parameters.get('titleBlacklist', []) or []
        self.positions = parameters.get('positions', [])
        self.locations = parameters.get('locations', [])
        self.apply_once_at_company = parameters.get('applyOnceAtCompany', False)
        self.base_search_url = self.get_base_search_url(parameters)
        self.seen_jobs = []
        resume_path = parameters.get('uploads', {}).get('resume', None)
        self.resume_path = Path(resume_path) if resume_path and Path(resume_path).exists() else None
        self.output_file_directory = Path(parameters['outputFileDirectory'])
        self.env_config = EnvironmentKeys()
        logger.debug("Parameters set successfully")

    def set_gpt_answerer(self, gpt_answerer):
        logger.debug("Setting GPT answerer")
        self.gpt_answerer = gpt_answerer

    def set_resume_generator_manager(self, resume_generator_manager):
        logger.debug("Setting resume generator manager")
        self.resume_generator_manager = resume_generator_manager

    def start_applying(self):
        logger.debug("Starting job application process")
        self.easy_applier_component = LinkedInEasyApplier(self.driver, self.resume_path, self.set_old_answers,
                                                          self.gpt_answerer, self.resume_generator_manager)
        searches = list(product(self.positions, self.locations))
        random.shuffle(searches)
        page_sleep = 0
        minimum_time = 60 * 5
        minimum_page_time = time.time() + minimum_time

        for position, location in searches:
            location_url = "&location=" + location
            job_page_number = -1
            utils.printyellow(f"Starting the search for {position} in {location}.")

            try:
                while True:
                    page_sleep += 1
                    job_page_number += 1
                    utils.printyellow(f"Going to job page {job_page_number}")
                    self.next_job_page(position, location_url, job_page_number)
                    time.sleep(random.uniform(1.5, 3.5))
                    utils.printyellow("Starting the application process for this page...")

                    try:
                        jobs = self.get_jobs_from_page()
                        if not jobs:
                            utils.printyellow("No more jobs found on this page. Exiting loop.")
                            break
                    except Exception as e:
                        logger.error(f"Failed to retrieve jobs: {e}")
                        break

                    try:
                        self.apply_jobs()
                    except Exception as e:
                        logger.error("Error during job application: %s", e)
                        utils.printred(f"Error during job application: {e}")
                        continue

                    utils.printyellow("Applying to jobs on this page has been completed!")

                    time_left = minimum_page_time - time.time()
                    if time_left > 0:
                        utils.printyellow(f"Sleeping for {time_left} seconds.")
                        logger.debug("Sleeping for %d seconds", time_left)
                        time.sleep(time_left)
                        minimum_page_time = time.time() + minimum_time
                    if page_sleep % 5 == 0:
                        sleep_time = random.randint(5, 34)
                        utils.printyellow(f"Sleeping for {sleep_time / 60} minutes.")
                        logger.debug("Sleeping for %d seconds", sleep_time)
                        time.sleep(sleep_time)
                        page_sleep += 1
            except Exception as e:
                logger.error("Unexpected error during job search: %s", e)
                utils.printred(f"Unexpected error: {e}")
                continue
            time_left = minimum_page_time - time.time()
            if time_left > 0:
                utils.printyellow(f"Sleeping for {time_left} seconds.")
                logger.debug("Sleeping for %d seconds", time_left)
                time.sleep(time_left)
                minimum_page_time = time.time() + minimum_time
            if page_sleep % 5 == 0:
                sleep_time = random.randint(50, 90)
                utils.printyellow(f"Sleeping for {sleep_time / 60} minutes.")
                logger.debug("Sleeping for %d seconds", sleep_time)
                time.sleep(sleep_time)
                page_sleep += 1

    def get_jobs_from_page(self):

        try:

            no_jobs_element = self.driver.find_element(By.CLASS_NAME, 'jobs-search-two-pane__no-results-banner--expand')
            if 'No matching jobs found' in no_jobs_element.text or 'unfortunately, things aren' in self.driver.page_source.lower():
                utils.printyellow("No matching jobs found on this page.")
                logger.debug("No matching jobs found on this page, skipping.")
                return []

        except NoSuchElementException:
            pass

        try:
            job_results = self.driver.find_element(By.CLASS_NAME, "jobs-search-results-list")
            utils.scroll_slow(self.driver, job_results)
            utils.scroll_slow(self.driver, job_results, step=300, reverse=True)

            job_list_elements = self.driver.find_elements(By.CLASS_NAME, 'scaffold-layout__list-container')[
                0].find_elements(By.CLASS_NAME, 'jobs-search-results__list-item')
            if not job_list_elements:
                utils.printyellow("No job class elements found on page.")
                logger.debug("No job class elements found on page, skipping.")
                return []

            return job_list_elements

        except NoSuchElementException:
            logger.debug("No job results found on the page.")
            return []

        except Exception as e:
            logger.error(f"Error while fetching job elements: {e}")
            return []

    def apply_jobs(self):
        try:
            no_jobs_element = self.driver.find_element(By.CLASS_NAME, 'jobs-search-two-pane__no-results-banner--expand')
            if 'No matching jobs found' in no_jobs_element.text or 'unfortunately, things aren' in self.driver.page_source.lower():
                utils.printyellow("No matching jobs found on this page, moving to next.")
                logger.debug("No matching jobs found on this page, skipping")
                return
        except NoSuchElementException:
            pass

        job_results = self.driver.find_element(By.CLASS_NAME, "jobs-search-results-list")
        utils.scroll_slow(self.driver, job_results)
        utils.scroll_slow(self.driver, job_results, step=300, reverse=True)
        job_list_elements = self.driver.find_elements(By.CLASS_NAME, 'scaffold-layout__list-container')[
            0].find_elements(By.CLASS_NAME, 'jobs-search-results__list-item')
        if not job_list_elements:
            utils.printyellow("No job class elements found on page, moving to next page.")
            logger.debug("No job class elements found on page, skipping")
            return
        job_list = [Job(*self.extract_job_information_from_tile(job_element)) for job_element in job_list_elements]
        for job in job_list:
            if self.is_blacklisted(job.title, job.company, job.link):
                utils.printyellow(f"Blacklisted {job.title} at {job.company}, skipping...")
                logger.debug("Job blacklisted: %s at %s", job.title, job.company)
                self.write_to_file(job, "skipped")
                continue
            if self.is_already_applied_to_job(job.title, job.company, job.link):
                self.write_to_file(job, "skipped")
                continue            
            if self.is_already_applied_to_company(job.company):
                self.write_to_file(job, "skipped")
                continue
            try:
                if job.apply_method not in {"Continue", "Applied", "Apply"}:
                    self.easy_applier_component.job_apply(job)
                    self.write_to_file(job, "success")
                    logger.debug("Applied to job: %s at %s", job.title, job.company)
            except Exception as e:
                logger.error("Failed to apply for %s at %s: %s", job.title, job.company, e)
                utils.printred(f"Failed to apply for {job.title} at {job.company}: {e}")
                self.write_to_file(job, "failed")
                continue

    def write_to_file(self, job, file_name):
        logger.debug("Writing job application result to file: %s", file_name)
        pdf_path = Path(job.pdf_path).resolve()
        pdf_path = pdf_path.as_uri()
        data = {
            "company": job.company,
            "job_title": job.title,
            "link": job.link,
            "job_recruiter": job.recruiter_link,
            "job_location": job.location,
            "pdf_path": pdf_path
        }
        file_path = self.output_file_directory / f"{file_name}.json"
        if not file_path.exists():
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump([data], f, indent=4)
                logger.debug("Job data written to new file: %s", file_path)
        else:
            with open(file_path, 'r+', encoding='utf-8') as f:
                try:
                    existing_data = json.load(f)
                except json.JSONDecodeError:
                    logger.error("JSON decode error in file: %s", file_path)
                    existing_data = []
                existing_data.append(data)
                f.seek(0)
                json.dump(existing_data, f, indent=4)
                f.truncate()
                logger.debug("Job data appended to existing file: %s", file_path)

    def get_base_search_url(self, parameters):
        logger.debug("Constructing base search URL")
        url_parts = []
        if parameters['remote']:
            url_parts.append("f_CF=f_WRA")
        experience_levels = [str(i + 1) for i, (level, v) in enumerate(parameters.get('experienceLevel', {}).items()) if
                             v]
        if experience_levels:
            url_parts.append(f"f_E={','.join(experience_levels)}")
        url_parts.append(f"distance={parameters['distance']}")
        job_types = [key[0].upper() for key, value in parameters.get('jobTypes', {}).items() if value]
        if job_types:
            url_parts.append(f"f_JT={','.join(job_types)}")
        date_mapping = {
            "all time": "",
            "month": "&f_TPR=r2592000",
            "week": "&f_TPR=r604800",
            "24 hours": "&f_TPR=r86400"
        }
        date_param = next((v for k, v in date_mapping.items() if parameters.get('date', {}).get(k)), "")
        url_parts.append("f_LF=f_AL")  # Easy Apply
        base_url = "&".join(url_parts)
        full_url = f"?{base_url}{date_param}"
        logger.debug("Base search URL constructed: %s", full_url)
        return full_url

    def next_job_page(self, position, location, job_page):
        logger.debug("Navigating to next job page: %s in %s, page %d", position, location, job_page)
        self.driver.get(
            f"https://www.linkedin.com/jobs/search/{self.base_search_url}&keywords={position}{location}&start={job_page * 25}")

    def extract_job_information_from_tile(self, job_tile):
        logger.debug("Extracting job information from tile")
        job_title, company, job_location, apply_method, link = "", "", "", "", ""
        try:
            job_title = job_tile.find_element(By.CLASS_NAME, 'job-card-list__title').text
            link = job_tile.find_element(By.CLASS_NAME, 'job-card-list__title').get_attribute('href').split('?')[0]
            company = job_tile.find_element(By.CLASS_NAME, 'job-card-container__primary-description').text
            logger.debug("Job information extracted: %s at %s", job_title, company)
        except NoSuchElementException:
            utils.printyellow("Some job information (title, link, or company) is missing.")
            logger.warning("Some job information (title, link, or company) is missing.")
        try:
            job_location = job_tile.find_element(By.CLASS_NAME, 'job-card-container__metadata-item').text
        except NoSuchElementException:
            utils.printyellow("Job location is missing.")
            logger.warning("Job location is missing.")
        try:
            apply_method = job_tile.find_element(By.CLASS_NAME, 'job-card-container__apply-method').text
        except NoSuchElementException:
            apply_method = "Applied"
            utils.printyellow("Apply method not found, assuming 'Applied'.")
            logger.warning("Apply method not found, assuming 'Applied'.")

        return job_title, company, job_location, link, apply_method

    def is_blacklisted(self, job_title, company, link):
        logger.debug("Checking if job is blacklisted: %s at %s", job_title, company)
        job_title_words = job_title.lower().split(' ')
        title_blacklisted = any(word in job_title_words for word in self.title_blacklist)
        company_blacklisted = company.strip().lower() in (word.strip().lower() for word in self.company_blacklist)
        link_seen = link in self.seen_jobs

        is_blacklisted = title_blacklisted or company_blacklisted or link_seen
        logger.debug("Job blacklisted status: %s", is_blacklisted)
        return is_blacklisted

        return title_blacklisted or company_blacklisted or link_seen

    def is_already_applied_to_job(self, job_title, company, link):
        link_seen = link in self.seen_jobs
        if link_seen:
            utils.printyellow(f"Already applied to job: {job_title} at {company}, skipping...")
        return link_seen

    def is_already_applied_to_company(self, company):
        if not self.apply_once_at_company:
            return False  
        
        output_files = ["success.json"]
        for file_name in output_files:
            file_path = self.output_file_directory / file_name
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    try:
                        existing_data = json.load(f)
                        for applied_job in existing_data:
                            if applied_job['company'].strip().lower() == company.strip().lower():
                                utils.printyellow(f"Already applied at {company} (once per company policy), skipping...")
                                return True
                    except json.JSONDecodeError:
                        continue
        return False

