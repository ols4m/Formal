# Quick Start Guide

## ✅ Setup Complete!

Your `powerschool_scraper.py` now has:
- ✅ Google SSO authentication
- ✅ School portal login
- ✅ Credential input from user
- ✅ Browser automation with Playwright

## 🚀 How to Run

1. **Just run the script:**
   ```bash
   python powerschool_scraper.py
   ```

2. **You'll be prompted for:**
   - School Email (e.g., yourname@school.edu)
   - Username (your school username)
   - Password (your school password)
   - PowerSchool Login URL (the starting page)

3. **What happens:**
   - Browser opens (visible, not headless)
   - Navigates to PowerSchool
   - Clicks Google login
   - Enters your email
   - Enters your username
   - Enters your password
   - Handles verification if needed
   - Logs you into PowerSchool!

4. **After login:**
   - Browser stays open for you to explore
   - Press Ctrl+C when done to close

## 🔧 Customization Points

If the automation doesn't work perfectly, you may need to adjust these selectors in the code:

**Google Login Button:** (line ~61)
```python
google_button = await page.wait_for_selector(
    'button:has-text("Google"), a:has-text("Google")'
)
```

**Post-Login Verification:** (line ~145)
```python
await page.wait_for_selector(
    '.student-name, #userName'
)
```

## 📝 Next Steps

Once login is working:
1. Explore PowerSchool with F12 (Developer Tools)
2. Find the CSS selectors for grades/classes/assignments
3. Add scraping logic to extract that data
4. Use `powerschool_scraper_example.py` as reference for data extraction

## 🐛 Troubleshooting

**Login button not found:**
- The script will pause for 5 seconds
- Manually click the Google login button
- Script will continue automatically

**Can't find username/password fields:**
- Check the selector names in your school's portal
- Inspect with F12 and update the selectors in code

**Popup not switching:**
- Script handles both popup and redirect flows
- Should work automatically

## 💡 Tips

- Run with browser visible first (`headless=False`) to debug
- Use F12 to inspect elements and find selectors
- Test with real credentials in a safe environment
- Never commit credentials to version control!
