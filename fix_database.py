import sqlite3

def fix_database():
    conn = sqlite3.connect('lms.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("ALTER TABLE announcements ADD COLUMN audience TEXT DEFAULT 'teachers'")
        print("Added audience column")
    except:
        print("audience column already exists")
    
    try:
        cursor.execute("ALTER TABLE submissions ADD COLUMN file_path TEXT")
        print("Added file_path column")
    except:
        print("file_path column already exists")
    
    conn.commit()
    conn.close()
    print("Database fixed!")

if __name__ == '__main__':
    fix_database()