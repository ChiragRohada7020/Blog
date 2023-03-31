from flask import Flask
from flask import Flask, jsonify, request, make_response
from flask import render_template
import json
from pymongo import MongoClient
from datetime import datetime

app = Flask(__name__)

if __name__ == '__main__':
    app.run(host='0.0.0.0')

client = MongoClient("mongodb+srv://Blog:Blog12345@atlascluster.t7vxr4g.mongodb.net/test")

db = client.Blog


@app.route('/')
def Home():
    data=db['lifestyle'].find().sort([("date", 1)]).limit(3)
    data2 = db['work'].find().sort([("date", 1)]).limit(3)

    return render_template('index.html',data=data,data2=data2)




@app.route('/about_us')
def About():
 
    return render_template('about_us.html')

@app.route('/add_blog',methods =["GET", "POST"])
def Add_blog():
    # data=db['lifestyle'].find().sort([("date", 1)]).limit(3)
    # data2 = db['work'].find().sort([("date", 1)]).limit(3)
    if request.method == "POST":
       # getting input with name = fname in HTML form
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
    return render_template('travel.html',data=data)

@app.route('/work')
def Work():
    mycollection = db["work"]
    data=mycollection.find()
    return render_template('work.html',data=data)


@app.route('/blog/<url>')
def Blog(url):
    mycollection = db["lifestyle"]
    data=mycollection.find_one({'url': url})
    if data:
        print("ok")
    else:
        mycollection = db["work"]
        data=mycollection.find_one({'url': url})

    
    return render_template('blog.html',data=data)