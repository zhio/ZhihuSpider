CREATE DATABASE zhihu_spider
DEFAULT CHARACTER SET utf8
DEFAULT COLLATE utf8_general_ci;

use zhihu_spider;

CREATE TABLE user_info
(
	id int auto_increment primary key,
    user_avator_url varchar(50),
    user_token varchar(50) unique not null,
    user_name varchar(30) not null,
    user_headline varchar(100),
    user_location varchar(100),
    user_business varchar(50),
    user_employments varchar(100),
    user_educations varchar(100),
    user_description varchar(150),
    user_sinaweibo_url varchar(50),
    user_gender int,
    user_following_count int,
    user_follower_count int,
    user_answer_count int,
    user_question_count int,
    user_voteup_count int,
    index(user_token)
)ENGINE=InnoDB DEFAULT CHARACTER SET=utf8;

CREATE TABLE user_list_cache
(
	user_token varchar(50) primary key
)ENGINE=InnoDB DEFAULT CHARACTER SET=utf8;

CREATE TABLE analysed_user_list_cache
(
	user_token varchar(50) primary key,
    following_count int not null,
    follower_count int not null
)ENGINE=InnoDB DEFAULT CHARACTER SET=utf8;
