package com.example.mapper;

import com.example.model.User;
import java.util.List;

public interface UserMapper {

    User findById(Long id);

    List<User> findAll();

    int insertUser(User user);

    int updateUser(User user);

    int deleteById(Long id);
}
