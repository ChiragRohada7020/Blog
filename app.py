from flask import Flask
from flask import Flask,flash, jsonify, request, make_response
from flask import render_template, redirect, url_for
import json
import os

from pymongo import MongoClient
from datetime import datetime
from flask_mail import Mail, Message


app = Flask(__name__)

if __name__ == '__main__':
    app.run(host='0.0.0.0')

client = MongoClient("mongodb+srv://Blog:Blog12345@atlascluster.t7vxr4g.mongodb.net/test")
app.secret_key=os.urandom(24)

db = client.Blog


mail = Mail(app) # instantiate the mail class
   
# configuration of mail
app.config['MAIL_SERVER']='smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USERNAME'] = 'rohadas.blogs@gmail.com'
app.config['MAIL_PASSWORD'] = 'vmwcppijzubzxuxp'
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USE_SSL'] = True
mail = Mail(app)

@app.route('/')
def Home():
    try:
        data=db['lifestyle'].find().sort([("date", 1)]).limit(3)
        data2 = db['work'].find().sort([("date", 1)]).limit(3)

        return render_template('index.html',data=data,data2=data2)
    except:
        return "Error"




@app.route('/about_us')
def About():
 
    return render_template('about_us.html')

@app.route('/add_blog',methods =["GET", "POST"])
def Add_blog():

    try:
        if request.method == "POST":
            heading = request.form.get("heading")
            discription = request.form.get("discription")
            body1 = request.form.get("body1")
            body2 = request.form.get("body2")
            heading2 = request.form.get("heading2")
            url = request.form.get("url")
            info = request.form.get("info")
            date = request.form.get("date")
            img1= request.form.get("img1")
            img2= request.form.get("img2")
            m_img = request.files['m_img']
            m_img.save('static/images/post_images/' + m_img.filename)
            x=m_img.filename.split(".")
            img1 = request.files['img1']
            img1.save('static/images/post_images/' + img1.filename)
            y=img1.filename.split(".")
            print(x[0])
       
            mycollection = db[info]
            date_object = datetime.strptime(date, '%Y-%m-%d')
            mydict = {"heading": heading, "url": url,"discription":discription,"date":date_object,"m-img":x[0],"img1":y[0],"body1":body1,"body2":body2,"heading2":heading2}

            data=mycollection.insert_one(mydict)
            mycollection = db["email"]
            users=mycollection.distinct( "email" )
            email_list=[]
            for i in users:
                email_list.append(i)
            print(email_list)
            msg = Message(
                subject="Rohadas Blog",
                sender ='rohadas.blogs@gmail.com',
                recipients = email_list
               )
            msg.html =render_template('email.html',heading=heading,discription=discription,url=url)
      
            mail.send(msg)
            return render_template('add_detail.html')
    except:
        return render_template('error.html')
        
        

       # getting input with name = fname in HTML form

    # data=db['lifestyle'].find().sort([("date", 1)]).limit(3)
    # data2 = db['work'].find().sort([("date", 1)]).limit(3)
    
    #    print(date)
       

    return render_template('add_detail.html')

@app.route('/search/<input>')
def Search(input):
    mycollection = db["lifestyle"]
    
    
    # mycollection.create_index([("heading", 'text')])
    result = mycollection.find({'$text': {'$search': input}})
    list=[]
    for i in result:
        
        list.append({"heading":i['heading'],"url":i['url']})
    mycollection = db["work"]
    
    
    # mycollection.create_index([("heading", 'text')])
    result = mycollection.find({'$text': {'$search': input}})
    for i in result:
        
        list.append({"heading":i['heading'],"url":i['url']})
    print(list)
    
    return list


@app.route('/travel')
def Travel():
    mycollection = db["lifestyle"]
    data=mycollection.find()
    mycollection = db["email"]
    
   
    
    return render_template('travel.html',data=data)

@app.route('/work')
def Work():
    mycollection = db["work"]
    data=mycollection.find()
    return render_template('work.html',data=data)



@app.route('/email_subscribe',methods =["GET", "POST"])
def Email():
    try:

        if request.method == "POST":
       # getting input with name = fname in HTML form
            email = request.form.get("email")
            mycollection = db["email"]
            
            data=mycollection.insert_one({"email":email})
            flash('You were successfully logged in')
            return redirect(url_for('Home'))
    except:
        return render_template("error.html")

@app.route('/blog/<url>')
def Blog(url):
    try:
        mycollection = db["lifestyle"]
        data = mycollection.find_one({'url': url})
        mycollection = db["email"]
        users = mycollection.email.distinct("email")

        if not data:
            mycollection = db["work"]
            data = mycollection.find_one({'url': url})
        print(data)

        # Splitting body1 content into paragraphs based on newline characters
        if 'body1' in data:
            data['body1'] = data['body1'].split('\n')

        return render_template('blog.html', data=data)
    except:
        return render_template('error.html')
