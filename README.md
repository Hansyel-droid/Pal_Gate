# Installation & Setup

## 1. Clone the Repository
Download the project to your local machine:

```bash
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name
```
## 2. Set Up Virtual Environment
It is highly recommended to use a virtual environment to avoid library conflicts.

```
# Create venv
python -m venv venv

# Activate venv (Windows)
venv\Scripts\activate

# Activate venv (Mac/Linux)
source venv/bin/activate
```
## 3. Install Dependencies
Install all necessary Python libraries using the requirements file:

``` 
pip install -r requirements.txt
```

## 4. Apply Database Migrations
Prepare the local database:

```
python manage.py makemigrations
python manage.py migrate
```

# User Roles & Configuration
To test the different views of the system (Applicant, Guard, and Sticker Administrator), you need to create administrative accounts and assign them roles manually in the admin panel.

## 1. Create Superusers
Run the following command in your terminal **three (3) times** to create three separate accounts:

```
python manage.py createsuperuser
```

## 2. Assigning Roles in Django Admin
After creating the users, you must assign their specific "User Type" roles:

- Start the server: ``` python manage.py runserver ```

- Open Admin Panel: http://127.0.0.1:8000/admin

- Log in with one of your superuser accounts.

- Navigate to Accounts > Users.

- Click on the Username of the superuser you want to configure.

- Set Role: Scroll down to the bottom of the page.

- Select User Type: Locate the user type selection and choose the appropriate role for that account:

**1. Student**

**2. Instructor/Staff**

**3. Security Guard**

Save: Click the Save button. Repeat these steps for the other two superusers.

# Running the Application
Once the roles are set, you can run the development server:

```
python manage.py runserver
```

Guard and Sticker Admin Portal: http://127.0.0.1:8000/

Applicant Portal: http://127.0.0.1:8000/accounts/login/applicant

Admin Dashboard: http://127.0.0.1:8000/admin