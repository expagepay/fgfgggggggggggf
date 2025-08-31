from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import json
import os

auth_google = 'https://accounts.google.com/signin'

opt = webdriver.ChromeOptions()
base_path = r"C:\Users\Desk\AppData\Local\Google\Chrome\User Data"
opt.add_argument(f"user-data-dir={base_path}")
opt.add_argument("profile-directory=Profile 7")
opt.add_argument("--no-sandbox")
opt.add_argument("--disable-dev-shm-usage")
opt.add_argument("--disable-gpu")
nav = webdriver.Chrome(options=opt)

nav.get(auth_google)

input_email = WebDriverWait(nav, 20).until(
    EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type='email']"))
)

input_email.send_keys('soareseliseu190@gmail.com')

button_next = WebDriverWait(nav, 20).until(
    EC.element_to_be_clickable((By.XPATH, "/html/body/div[2]/div[1]/div[2]/c-wiz/main/div[2]/div/div/div/form/span/section[2]/div/div/div[1]/div[1]/div/div/div/div/div[1]/div/div[1]/input"))
)

button_next.click()

input_pass = WebDriverWait(nav, 20).until(
    EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
)

button_next.click()
input_pass.send_keys('mnoklgfv')