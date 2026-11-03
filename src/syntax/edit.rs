//! A byte edit. `old_end` is exclusive in the original source.

#[derive(Clone, Debug)]
pub struct Edit {
    pub start: usize,
    pub old_end: usize,
    pub new_text: String,
}

impl Edit {
    pub fn apply(&self, source: &str) -> String {
        let mut out = String::with_capacity(source.len() + self.new_text.len());
        out.push_str(&source[..self.start]);
        out.push_str(&self.new_text);
        out.push_str(&source[self.old_end..]);
        out
    }

    pub fn delta(&self) -> isize {
        self.new_text.len() as isize - (self.old_end as isize - self.start as isize)
    }
}
