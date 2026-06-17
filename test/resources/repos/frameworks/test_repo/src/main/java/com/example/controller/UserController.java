package com.example.controller;

import com.example.mapper.UserMapper;
import com.example.model.User;
import java.util.List;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

@Controller
public class UserController {

    private final UserMapper userMapper;

    public UserController(UserMapper userMapper) {
        this.userMapper = userMapper;
    }

    @GetMapping("/users/{id}")
    public String userDetail(@PathVariable Long id, Model model) {
        User user = userMapper.findById(id);
        List<User> items = userMapper.findAll();
        model.addAttribute("user", user);
        model.addAttribute("items", items);
        model.addAttribute("pageTitle", "User Detail");
        model.addAttribute("isAdmin", false);
        return "user_detail";
    }
}
